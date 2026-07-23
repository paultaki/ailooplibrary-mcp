# Build image for Glama's MCP quality scan (Deploy → Make Release).
# The server is a single file, Python 3.9+ stdlib only — nothing to pip install.
# Glama runs this container and speaks newline-delimited JSON-RPC 2.0 over stdio.
FROM python:3.12-slim

WORKDIR /app
COPY server.py .

# The tool definitions Glama grades (tools/list) are static, so no catalog
# access is required to score. Pin the catalog URL anyway so any tool the
# scanner actually invokes resolves against the live site.
ENV AI_LOOP_LIBRARY_CATALOG_URL=https://ailooplibrary.com/catalog.json

ENTRYPOINT ["python3", "server.py"]
