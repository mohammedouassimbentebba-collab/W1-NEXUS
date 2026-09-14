# مثال Long-Term Memory

بعد تثبيت W1 Nexus:

```bash
w1 --workspace ./memory-demo init

w1 --workspace ./memory-demo memory add \
  --file examples/memory/pump-supply-voltage.json \
  --project pump-project

w1 --workspace ./memory-demo memory add \
  --file examples/memory/pump-controller-relationship.json \
  --project pump-project

w1 --workspace ./memory-demo memory search "pump controller voltage" \
  --project pump-project

w1 --workspace ./memory-demo memory context "pump controller current" \
  --project pump-project \
  --token-budget 256

w1 --workspace ./memory-demo memory graph \
  --project pump-project \
  --anchor "battery pack" \
  --depth 2

w1 --workspace ./memory-demo memory verify
```

القيم الموجودة في الأمثلة تركيبية ولا تمثل مواصفات منتج حقيقي.
