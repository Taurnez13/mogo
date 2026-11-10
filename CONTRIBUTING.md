# Contributing

MOGO is small on purpose. Before adding a dependency or a JavaScript build step, ask whether
a publisher with no IT department could still run it from `./run.sh`.

## Dev setup

```bash
./run.sh                      # http://localhost:8000, reloads on change
python tests/test_roundtrip.py
```

The first visit shows the setup page. `examples/sample_onix30.xml` is a small fictional catalogue
to import.

## Where things live

* `app/models.py` — one SQLAlchemy class per ONIX composite. If you add a field, add it to the
  importer (`app/onix/importer.py`), the exporter (`app/onix/exporter.py`) and, if users edit it,
  `app/web.py` + the template.
* The exporter must keep producing schema-valid ONIX: `tests/test_roundtrip.py` validates against
  the EDItEUR XSD and fails on any error. Run it before opening a pull request.
* Code lists come from `app/onix/data/codelists.json`, generated from the EDItEUR schema files.

## Good first contributions

* A recipient profile (Ingram, Bowker, BookNet, Amazon Vendor): see `Recipient` in models and the
  export route.
* An e-commerce connector (WooCommerce, Squarespace) following the shape of `app/connectors/shopify.py`.
* ONIX 2.1 output for recipients that still require it.
* Thema/BIC code pickers with search.
* Translations of the UI.

Please keep pull requests focused and include a sentence on how you tested.
