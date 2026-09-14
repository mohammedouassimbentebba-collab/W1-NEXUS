# تجربة MCP محلية

بعد تثبيت W1 Nexus:

```bash
w1 --workspace ./mcp-demo init
w1 --workspace ./mcp-demo mcp servers add demo \
  --transport stdio \
  --command-json '["python","-m","w1cip.mcp_demo_server"]' \
  --auto-approve-tool echo

w1 --workspace ./mcp-demo mcp probe demo
w1 --workspace ./mcp-demo tools list --server demo
```

أنشئ `args.json`:

```json
{"text":"hello from W1"}
```

ثم:

```bash
w1 --workspace ./mcp-demo tools call \
  --call-id demo-echo-one \
  --name mcp.demo.echo \
  --arguments args.json \
  --server demo
```
