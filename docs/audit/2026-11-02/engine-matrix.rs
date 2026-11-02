//! Offline audit only: no HTTP requests or database operations are executed.
use rustxng::engine::EngineResponse;
use rustxng::{EngineRegistry, Query, Settings};
use std::panic::{AssertUnwindSafe, catch_unwind};

fn main() {
    std::panic::set_hook(Box::new(|_| {}));
    let settings = Settings::defaults();
    let registry = EngineRegistry::from_settings(&settings).unwrap();
    let mut query = Query::parse("rust 東京 & Unicode 😀", &registry, &[]);
    query.locale = "ja-JP".into();
    let mut rows = Vec::new();
    for config in &settings.engines {
        let mut row = serde_json::json!({"name":config.name,"engine":config.engine,"disabled":config.disabled});
        match rustxng::engines::build(config) {
            Err(error) => row["initialisation_error"] = error.into(),
            Ok(engine) => {
                let mut cases = Vec::new();
                if !engine.is_offline() {
                    for page in [1, 2, u32::MAX] {
                        let outcome =
                            catch_unwind(AssertUnwindSafe(|| engine.build_request(&query, page)));
                        cases.push(serde_json::json!({"kind":"request","page":page,"panic":outcome.is_err(),"error":outcome.ok().and_then(Result::err).map(|e| e.to_string())}));
                    }
                }
                for (label, body) in [
                    ("empty", ""),
                    ("json_object", "{}"),
                    ("json_array", "[]"),
                    ("json_null", "null"),
                    ("html", "<html><body>東京 😀</body></html>"),
                    ("xml", "<?xml version=\"1.0\"?><rss><channel/></rss>"),
                ] {
                    let response =
                        EngineResponse::new(200, "https://example.com/", body.as_bytes().to_vec());
                    let outcome =
                        catch_unwind(AssertUnwindSafe(|| engine.parse(&response, &query)));
                    cases.push(serde_json::json!({"kind":"parse","fixture":label,"panic":outcome.is_err(),"error":outcome.ok().and_then(Result::err).map(|e|e.to_string())}));
                }
                row["cases"] = cases.into();
            }
        }
        rows.push(row);
    }
    println!("{}", serde_json::to_string_pretty(&rows).unwrap());
}
