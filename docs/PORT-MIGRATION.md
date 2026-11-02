# Default port migration: 8888 → 29483

This checkout changes the default HTTP port to **29483**, with loopback binding unchanged.
Published v0.26.0 binaries/images keep their original defaults. No published artifact is modified.

## Selection

Checked on 2026-11-02 against the [IANA registry](https://www.iana.org/assignments/service-names-port-numbers/):
29483 is inside the unassigned range 29170–29998. This is an application default, not an IANA assignment
or reservation. The registry can change, and unregistered software can use the same port.

28473 was considered, but [LeechAgent documents it as a listening port](https://github.com/ufrisk/LeechCore/wiki/LeechAgent).
A search of existing usage did not identify a widely documented default service on 29483;
individual game servers do use custom ports in this range. A free port on your machine must still be checked.

## Native and existing installations

Use the same explicit command with old or new binaries:

```bash
SEARXNG_PORT=29483 rustxng --bind 127.0.0.1 --port 29483
```

To preserve an existing 8888 deployment:

```bash
rustxng --bind 127.0.0.1 --port 8888
```

Alternatives are `SEARXNG_PORT=8888` or `port = 8888` under `[server]` in a configuration file.
CLI `--port` takes precedence over environment and configuration.
The example also sets `SEARXNG_PORT` because v0.26.0 generated absolute URLs from configuration
even when the listener used a CLI override; this checkout fixes that mismatch. Update the endpoint in clients,
health checks, service units, and reverse proxies when migrating. Existing services are not restarted automatically.

## Docker

Builds from this checkout listen on 29483 internally. Use explicit arguments with published images
so the command also works with older images:

```bash
docker run --rm -p 127.0.0.1:29483:29483 -e SEARXNG_PORT=29483 ghcr.io/sahenjp/rustxng:latest --bind 0.0.0.0 --port 29483
```

To keep the legacy host endpoint with a new image:

```bash
docker run --rm -p 127.0.0.1:8888:29483 rustxng --bind 0.0.0.0 --port 29483
```

`EXPOSE` documents the container port; it does not publish a host port.
Specify a host IP intentionally when external access is required; configure access controls for that deployment.

## rdsh

Set the endpoint explicitly instead of relying on a client's legacy 8888 fallback:

```bash
RUSTXNG_URL=http://127.0.0.1:29483 rdsh search-web --engine rustxng "query"
```

If `SEARXNG_URL` is already set, it may take precedence; update or unset it intentionally.
See [the rdsh guide](RDSH.md). This repository does not change rdsh's own discovery implementation.

## Compatibility and historical data

Explicit custom ports continue to work. Historical benchmarks and audit reproductions retain their original
ports and settings hashes; current setup examples, container configuration, and CI use 29483.
