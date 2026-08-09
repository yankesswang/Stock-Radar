"""Local data-scraping scripts package.

Marks this directory as a regular package so ``from scripts.* import ...``
resolves to these files. Required because a stray top-level ``scripts``
package (installed into site-packages by ``gguf``) would otherwise shadow
this directory when imported from the backend.
"""
