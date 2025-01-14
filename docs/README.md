# Sphinx Documentation

The documentation is built with [Sphinx](http://sphinx-doc.org/index.html). See their documentation for (a lot) more details.

## Installation

To generate the docs yourself, you'll need the appropriate package:

```
conda install sphinx
conda install sphinx_rtd_theme

pip install ghp-import
pip install sphinx-click
```

## Refreshing the API Documentation

- Make sure reV is in your PYTHONPATH
- Remove source/reV/reV.rst
- Run `sphinx-apidoc -eMT -o source/reV ../reV` from the `docs` folder.
- Add the following to the top of any CLI module's .rst file:
```
.. click:: module_path:main
   :prog: CLI-Alias # e.g. reV-gen
   :show-nested:
```
- `git push` changes to the documentation source code as needed.
- Make the documentation per below

## Building HTML Docs

### Mac/Linux

```
make html
```

### Windows

```
make.bat html
```

## Building PDF Docs

To build a PDF, you'll need a latex distribution for your system.

### Mac/Linux

```
make latexpdf
```

### Windows

```
make.bat latexpdf
```

## Pushing to GitHub Pages

### Mac/Linux

```
make github
```

### Windows

```
make.bat html
```

Then run the github-related commands by hand:

```
git branch -D gh-pages
git push origin --delete gh-pages
ghp-import -n -b gh-pages -m "Update documentation" ./_build/html
git checkout gh-pages
git push origin gh-pages
git checkout master # or whatever branch you were on
```
