# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# http://www.sphinx-doc.org/en/master/config

# -- Path setup --------------------------------------------------------------

import pathlib

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
import sys

import toml

root_dir = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root_dir / "src"))

_metadata = toml.load(str(root_dir / "pyproject.toml"))

# -- Project information -----------------------------------------------------

project = _metadata["tool"]["poetry"]["name"]
author = ", ".join(_metadata["tool"]["poetry"]["authors"])
copyright = "Witness Angel Project"

# The short X.Y version.
version = _metadata["tool"]["poetry"]["version"]
# The full version, including alpha/beta/rc tags.
release = version

# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = ["sphinx.ext.autodoc", "sphinx_autodoc_typehints", "sphinx_rtd_theme"]

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# The suffix of source filenames.
source_suffix = ".rst"

# The encoding of source files.
# source_encoding = 'utf-8-sig'

# The master toctree document.
master_doc = "index"

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Do not auto-transforms quotes and dashes
smartquotes = False

# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "sphinx_rtd_theme"

# Theme options are theme-specific and customize the look and feel of a theme
# further.  For a list of options available for each theme, see the
# documentation. See https://sphinx-rtd-theme.readthedocs.io/en/latest/configuring.html
html_theme_options = {"collapse_navigation": False}

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]


autodoc_default_options = {
    "members": True,
    #'member-order': 'bysource',
    #'special-members': '__init__',
    #'undoc-members': True,
    "show-inheritance": True,
    #'exclude-members': '__weakref__'
}
