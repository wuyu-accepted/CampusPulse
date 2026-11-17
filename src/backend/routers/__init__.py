"""Backend router package.

Routers are intentionally not imported eagerly. The dedicated CampusPulse
application must be able to load its allow-listed router without importing
the upstream code-generation, dynamic-simulation or filesystem surfaces.
The legacy backend continues to import its named submodules explicitly.
"""
