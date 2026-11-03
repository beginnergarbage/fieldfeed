from fieldfeed.urls import canonical, extract_links, is_story_link


def test_arxiv_variants_share_a_key():
    assert canonical("https://arxiv.org/abs/2610.12345v2") == "arxiv:2610.12345"
    assert canonical("https://arxiv.org/pdf/2610.12345") == "arxiv:2610.12345"


def test_tracking_params_and_www_are_dropped():
    a = canonical("https://www.example.com/post/foo/?utm_source=x&id=3")
    assert a == canonical("https://example.com/post/foo?id=3")


def test_twitter_and_x_match():
    assert canonical("https://twitter.com/a/status/123") == canonical("https://x.com/b/status/123")


def test_profiles_and_home_pages_are_not_stories():
    assert not is_story_link("https://x.com/someone")
    assert not is_story_link("https://www.ebi.ac.uk/")
    assert is_story_link("https://www.ebi.ac.uk/about/news/2026-11-01-some-long-post-name")


def test_svg_namespace_is_not_a_link():
    html = '<svg xmlns="http://www.w3.org/2000/svg"></svg> see <a href="https://arxiv.org/abs/2610.00001">paper</a>'
    assert [canonical(u) for u in extract_links(html)] == ["arxiv:2610.00001"]
