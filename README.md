# SupplySwap MCP Servers

Monorepo for all SupplySwap MCP (Model Context Protocol) servers — hosted on Google Cloud Run.

## Servers

| Folder | Description | Cloud Run URL |
|---|---|---|
| `google-chat-mcp/` | Google Chat — read/send messages, DMs, reactions | `https://google-chat-mcp-253940259390.us-central1.run.app/mcp` |
| `baselinker-mcp/` | BaseLinker — orders, inventory, CRM, shipping | `https://baselinker-mcp-253940259390.us-central1.run.app/mcp` |

## Connecting to an AI tool

### Google Chat MCP
- **Modern MCP URL:** `https://google-chat-mcp-253940259390.us-central1.run.app/mcp`
- **Legacy SSE URL:** `https://google-chat-mcp-253940259390.us-central1.run.app/sse`
- **Auth:** Bearer token — each user gets their own personal token
- **Onboarding:** visit the setup page, sign in with your `@supplyswap.com` Google account, and copy your token
  👉 **[https://google-chat-mcp-253940259390.us-central1.run.app/setup](https://google-chat-mcp-253940259390.us-central1.run.app/setup)**

### BaseLinker MCP
- **Modern MCP URL:** `https://baselinker-mcp-253940259390.us-central1.run.app/mcp`
- **Legacy SSE URL:** `https://baselinker-mcp-253940259390.us-central1.run.app/sse`
- **Auth:** API Key — use your BaseLinker API token directly
  - Key: `X-BLToken`
  - Value: your BaseLinker token (BaseLinker → Account → My Account → API)

## Deployment

Both servers are deployed to Google Cloud Run (project `named-perigee-491622-p1`, region `us-central1`).

To rebuild and deploy after changes:
```bash
cd google-chat-mcp   # or baselinker-mcp
gcloud builds submit --tag us-central1-docker.pkg.dev/named-perigee-491622-p1/cloud-run-source-deploy/<service-name>:latest --project=named-perigee-491622-p1
gcloud run deploy <service-name> --image ... --region us-central1 --project named-perigee-491622-p1
```
