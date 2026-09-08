# Memory viewer validation

- Full existing suite plus viewer tests: 239 passed. The subsequently added
  packaged-CLI test also passed in the 12-test viewer suite.
- Browser checks passed at 360, 736, and 1024 pixels in light and dark themes:
  list/detail separation, search, pagination, refresh, browser history, reload,
  missing data, empty profiles, and literal rendering of HTML-like teaching.
- A built release was extracted and validated. Its CLI read a separate installation
  home without creating metadata, migrating teaching, or modifying release files.
- Read-only inspection of the operator's existing profile returned 53 entries,
  including 45 numbered teachings and 4 structured lessons, with no warnings.
  50 entries had a saved interpretation or linked summary. Input bytes and
  modification times were unchanged. No personal content is included here.
- The live localhost list and a real teaching detail were visually checked.

Known limits: no semantic deduplication, automatic judgment of lesson quality,
editing, or inferred timestamps. Structured records retain separate scopes and
ending evidence even when linked to the same Q&A entry. The viewer displays
stored instructions; these checks do not prove how a model will apply them.
