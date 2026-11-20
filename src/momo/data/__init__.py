"""Data layer: raw NSE files -> clean, survivorship-free daily panel.

Stages (each is a separate module so it can be read and tested on its own):

    sources            where each public file lives (URL templates, local paths)
    download           polite, resumable downloader with a manifest
    parsers            one function per raw file layout -> tidy DataFrame
    corporate_actions  turn exchange "purpose" text into split factors and dividends
    identifiers        chain symbols and ISINs into one permanent id per company
    ingest             raw files -> yearly parquet files (interim)
    panel              interim -> adjusted returns, flags, market cap (processed)
    external           loaders for files the group supplies (risk-free rate, ...)
    quality            data-quality report the group must read before trusting results
    synthetic          test fixture generator (NOT a data source)
"""
