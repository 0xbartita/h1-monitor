import re
def test_package_imports():
    import h1monitor

    # Pinned to a shape, not a number: the CI guard already refuses to publish
    # a tag that disagrees with __version__, so restating the number here only
    # adds a test to edit at every release.
    assert re.fullmatch(r"\d+\.\d+\.\d+", h1monitor.__version__)
