from pathlib import Path

from w1cip import EmbeddedW1Runtime, ModelAccessFabric, ModelAccessStore

store = ModelAccessStore(Path(".w1nexus") / "model-access.sqlite3")
runtime = EmbeddedW1Runtime(ModelAccessFabric(store))

print("Models:")
for model in runtime.list_models():
    print("-", model["model_id"], model["access_mode"])

print("Portfolios:")
for portfolio in runtime.list_portfolios():
    print("-", portfolio["portfolio_id"], portfolio["strategy"])
