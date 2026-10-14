# Python API

The command line is built on the modules below; scripts can use them directly. The names each module lists in its
`__all__` are its documented API, and the most used ones are also importable from the top-level package:

```python
from nnnotes import Config, Session

session = Session(Config.load())  # the config file and the NNNOTES_* environment variables
catalog = session.catalog()  # the region's catalog, with its bundles
master = session.master_dir()  # the decoded master data directory
```

A missing or invalid setting raises `ConfigError`, naming the setting and never its value; a failed game API call
raises `GameApiError`. Other names are internal and can change in any version.

## Configuration and sources

::: nnnotes.source.config

::: nnnotes.source.session

::: nnnotes.source.catalog

::: nnnotes.source.addressables

::: nnnotes.source.master

::: nnnotes.source.gameapi

::: nnnotes.source.jp

## Music data

::: nnnotes.musicdata

::: nnnotes.musicdata.deckdata

## Site and schemas

::: nnnotes.site.webmodel

::: nnnotes.schemas
