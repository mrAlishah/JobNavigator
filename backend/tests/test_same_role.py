"""same_role.py: two postings with one title are one role only when the text says so word for word.

The cases are the ones found in 90 days of real postings (see the module docstring)."""
from backend.scraper._shared.same_role import (
    MIN_WORDS, is_template, other_words, role_words, same_text)

BODY = (
    "<p>About the team</p><p>The Payments Platform team builds the services that move money for "
    "millions of customers every day. As a Senior Product Manager you will own the roadmap for "
    "card issuing and settlement, partner with engineering, design, risk and finance, and turn "
    "customer research into clear product requirements. You will define success metrics, run "
    "experiments, and present results to leadership.</p><ul><li>5+ years of product management "
    "experience in payments or fintech</li><li>Experience shipping platform products used by other "
    "teams</li><li>Strong written communication and a habit of writing things down</li><li>Comfort "
    "with SQL and data analysis to answer your own questions</li></ul><p>We value curiosity, ownership "
    "and kindness. We offer health benefits, a learning budget, parental leave and a flexible "
    "schedule so you can do your best work wherever you are most productive.</p>")


def _with(**swap):
    text = BODY
    for old, new in swap.items():
        text = text.replace(old.replace("_", " "), new)
    return text


def test_the_body_is_long_enough_to_judge():
    assert len(role_words(BODY)) >= MIN_WORDS


def test_identical_text_is_one_role():
    assert same_text(BODY, BODY, "Seattle, WA", "New York, NY")


def test_a_per_city_copy_differs_only_in_place_pay_and_ids():
    seattle = BODY + "<p>Req R-482211-8. Based in Seattle, Washington. Pay $151,200.00 - $204,600.00 USD annually.</p>"
    new_york = BODY + "<p>Req R-482211-3. Based in New York, New York. Pay $166,300.00 - $225,000.00 USD annually.</p>"
    assert same_text(seattle, new_york, "Seattle, WA", "New York, NY")


def test_one_other_word_is_another_role():
    """Apple's Employee Experience PMs differ only in the area they name."""
    legal = _with(card_issuing_and_settlement="legal and finance tooling")
    music = _with(card_issuing_and_settlement="music and finance tooling")
    assert not same_text(legal, music)
    assert other_words(role_words(legal), role_words(music)) == 1


def test_years_of_experience_are_not_ignored_as_numbers():
    """Cisco: "4+ years" against "6+ years" is another level."""
    assert not same_text(BODY, BODY.replace("5+ years", "8+ years"))


def test_a_place_named_outside_the_locations_counts():
    """Plaid's copy named two more cities than its location field: not proven, kept apart."""
    assert not same_text(BODY, BODY + " Seattle or Raleigh.", "San Francisco HQ", None)


def test_common_words_of_a_location_field_still_count():
    """'Remotely in the UK' must not make 'remote' or 'in' ignorable."""
    remote = BODY + " This role is remote."
    hybrid = BODY + " This role is hybrid."
    assert not same_text(remote, hybrid, "Remotely in the UK", "London Office")


def test_too_little_text_proves_nothing():
    short = "Senior Product Manager. Join our team and build great products."
    assert not same_text(short, short)
    assert not same_text(None, BODY)
    assert not same_text("", "")


def test_markup_and_entities_do_not_count():
    other = BODY.replace("<p>", "<div>").replace("</p>", "</div>").replace("We value", "We&nbsp;value")
    assert same_text(BODY, other)


def test_a_text_the_company_uses_under_another_title_is_a_template():
    """Sierra's Spanish- and Portuguese-speaking strategists share one text."""
    assert is_template(BODY, [BODY.replace("Senior Product Manager", "Staff Product Manager")])
    assert not is_template(BODY, ["A different role entirely, about sales operations and quota planning."])
    assert not is_template(BODY, [])


def test_title_variants_are_one_title():
    """PayPal's "Sr" and DigitalOcean's dash against comma, both from real postings."""
    from backend.scraper._shared.same_role import title_key
    assert title_key("Sr Lead Product Manager") == title_key("Senior Lead Product Manager")
    assert title_key("Principal Product Manager - Inference Engine") == \
        title_key("Principal Product Manager, Inference Engine")
    assert title_key("Sr. Mgr, Product & Growth") == title_key("Senior Manager, Product and Growth")
    assert title_key("Software Engineer 2") == title_key("Software Engineer II")
    assert title_key("Product Manager (R-482211)") == title_key("Product Manager")


def test_titles_that_name_another_role_stay_apart():
    from backend.scraper._shared.same_role import title_key
    assert title_key("Software Engineer II") != title_key("Software Engineer III")
    assert title_key("Strategist (Spanish speaking)") != title_key("Strategist (Brazilian Portuguese speaking)")
    assert title_key("Product Manager, Safeguards (Cyber)") != title_key("Product Manager, Safeguards (Generalist)")
    assert title_key("Senior Product Manager") != title_key("Product Manager")


def _in(city):
    return BODY + f" This role is based in our {city} office."


def test_a_city_only_the_text_names_is_a_place_word():
    """DigitalOcean (Boston / Denver), Twitch (Seattle, WA / Irvine): one role per city, the city
    only in the text, the location field "United States"."""
    us = "United States"
    assert same_text(_in("Boston"), _in("Denver"), us, us)
    assert same_text(_in("Seattle, WA"), _in("Irvine"), us, us)
    assert same_text(_in("San Francisco Bay Area"), _in("New York City"), us, us)


def test_a_city_and_another_word_are_two_roles():
    us = "United States"
    assert not same_text(_in("Boston"), _in("Denver").replace("card issuing", "merchant onboarding"), us, us)


def test_places_that_are_not_cities_still_count():
    """Anthropic's Partner Success Lead for North America against EMEA is another role; names that
    are also words or people (Phoenix, Orange, Davis) stay words."""
    assert not same_text(_in("North America"), _in("EMEA"))
    assert not same_text(BODY + " Join Project Phoenix.", BODY + " Join Project Atlas.")
    assert not same_text(BODY + " Report to Jane Davis.", BODY + " Report to Jane Carson.")


def test_a_state_code_that_is_a_word_needs_its_comma():
    from backend.scraper._shared.same_role import text_places
    assert "in" in role_words("We hire in Boston in person")
    assert "in" not in role_words("Boston, IN")
    assert text_places("Offices in Seattle, WA and Irvine.") == {"seattle", "irvine"}
