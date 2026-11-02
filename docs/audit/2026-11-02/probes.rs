//! Audit probes assert observed defects, not the desired production behavior.
use axum::body::Body;
use axum::http::{Request, header};
use axum::{Router, routing::get};
use rustxng::engine::EngineRequest;
use rustxng::network::Network;
use rustxng::web::{AppState, image_proxy::is_public_url, router};
use rustxng::{Searcher, Settings};
use std::time::Duration;
use tower::ServiceExt;

#[test]
fn unrelated_partial_config_resets_embedded_cache_default() {
    use std::io::Write;
    let baseline = Settings::defaults();
    assert_eq!(baseline.cache.max_bytes, 32 * 1024 * 1024);
    let mut file = tempfile::NamedTempFile::new().unwrap();
    file.write_all(b"[server]\nport = 18888\n").unwrap();
    let loaded = Settings::load(Some(file.path())).unwrap();
    assert_eq!(loaded.server.port, 18888);
    assert_eq!(loaded.cache.max_bytes, 8 * 1024 * 1024);
}

#[test]
fn internal_addresses_pass_public_guard() {
    for url in [
        "http://localhost./",
        "http://[::ffff:127.0.0.1]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
    ] {
        assert!(is_public_url(url), "{url}");
    }
}

async fn server(app: Router) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    format!("http://{address}")
}

#[tokio::test]
async fn guarded_hostname_and_redirect_reach_loopback() {
    let app = Router::new()
        .route(
            "/redirect",
            get(|| async { axum::response::Redirect::temporary("/private") }),
        )
        .route("/private", get(|| async { "internal-only-data" }));
    let base = server(app).await.replace("127.0.0.1", "localhost.");
    let url = format!("{base}/redirect");
    assert!(is_public_url(&url));
    let network = Network::from_settings(&Settings::defaults()).unwrap();
    let response = network
        .fetch(EngineRequest::get(url), Duration::from_secs(2), None, None)
        .await
        .unwrap();
    assert_eq!(response.body, b"internal-only-data");
}

#[tokio::test]
async fn cache_reuses_response_across_cookie_values() {
    let app = Router::new().route(
        "/",
        get(|headers: axum::http::HeaderMap| async move {
            headers
                .get(header::COOKIE)
                .unwrap()
                .to_str()
                .unwrap()
                .to_string()
        }),
    );
    let base = server(app).await;
    let network = Network::from_settings(&Settings::defaults()).unwrap();
    let cache = rustxng::cache::Cache::default();
    let mut first = EngineRequest::get(format!("{base}/"));
    first.cookies = vec![("safe".into(), "strict".into())];
    let mut second = first.clone();
    second.cookies = vec![("safe".into(), "off".into())];
    let a = network
        .fetch(first, Duration::from_secs(2), Some(&cache), None)
        .await
        .unwrap();
    let b = network
        .fetch(second, Duration::from_secs(2), Some(&cache), None)
        .await
        .unwrap();
    assert_eq!(a.body, b.body);
    assert_eq!(b.body, b"safe=strict");
}

#[tokio::test]
async fn infinite_timeout_panics_in_public_handler() {
    let app = router(AppState::new(Searcher::new(Settings::defaults()).unwrap()));
    let task = tokio::spawn(async move {
        app.oneshot(
            Request::builder()
                .uri("/search?q=rust&format=json&timeout_limit=inf")
                .body(Body::empty())
                .unwrap(),
        )
        .await
    });
    assert!(task.await.unwrap_err().is_panic());
}

#[test]
fn javascript_url_survives_common_url_helpers() {
    let url =
        rustxng::engine::util::absolute_url("JaVaScRiPt:alert(1)", "https://example.com").unwrap();
    assert_eq!(url, "javascript:alert(1)");
    assert_eq!(
        rustxng::results::unwrap_redirect(&url).as_deref(),
        Some("javascript:alert(1)")
    );
}

#[tokio::test]
async fn research_bypasses_enabled_limiter() {
    let mut settings = Settings::defaults();
    settings.engines.clear();
    settings.server.limiter = rustxng::settings::LimiterSettings::Enabled(true);
    settings.server.limiter_max = 1;
    let app = router(AppState::new(Searcher::new(settings).unwrap()));
    for _ in 0..2 {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/research?q=rust")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), axum::http::StatusCode::OK);
    }
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/search?q=rust&format=json")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), axum::http::StatusCode::BAD_REQUEST);
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/search?q=rust&format=json")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), axum::http::StatusCode::TOO_MANY_REQUESTS);
    let response = app
        .oneshot(
            Request::builder()
                .uri("/research?q=rust")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), axum::http::StatusCode::OK);
}

#[tokio::test]
async fn javascript_result_reaches_html_link() {
    use http_body_util::BodyExt;
    let upstream = Router::new().route("/api/v1/search/", get(|| async {
        axum::Json(serde_json::json!([{"url":"javascript:alert(1)","title":[{"value":"rust documentation"}],"extract":[{"value":"rust programming reference"}]}]))
    }));
    let base = server(upstream).await;
    let mut settings = Settings::defaults();
    let mut config = rustxng::settings::EngineConfig::new("mwmbl", "mwmbl", "mwm", &["general"]);
    config.base_url = Some(format!("{base}/api/v1"));
    settings.engines = vec![config];
    let app = router(AppState::new(Searcher::new(settings).unwrap()));
    let response = app
        .oneshot(
            Request::builder()
                .uri("/search?q=rust")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let body = response.into_body().collect().await.unwrap().to_bytes();
    let html = String::from_utf8(body.to_vec()).unwrap();
    assert!(html.contains("href=\"javascript:alert(1)\""));
}
