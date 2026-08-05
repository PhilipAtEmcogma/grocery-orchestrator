"""
The price-data boundary.

  base.py     PriceRepository Protocol + PriceRecord — what the graph's
              retrieve_prices node depends on. Start here.
  memory.py   Fixture-backed implementation. Used for all local dev and
              tests; loads fixtures/products.json.
  dynamo.py   The real DynamoDB-backed implementation. Every method
              currently raises NotImplementedError on purpose — see its
              module docstring for why that is safer than a stub that
              returns an empty list.

Nodes depend only on the PriceRepository Protocol in base.py, never on
boto3, so the graph can be built and tested against fixtures with no AWS
account and later pointed at DynamoDB with no change to any node.
"""
