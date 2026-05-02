# Changelog

## [Unreleased]

### Internal cleanup
- Removed redundant string-level path-traversal check from `search_canon`; path-resolution check (`relative_to(base)`) remains.
