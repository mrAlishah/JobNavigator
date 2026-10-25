"""posting_links.py: a new job that repeats a posting the feed has is stored as its duplicate."""
from datetime import datetime, timedelta, timezone

from backend.models.db import Account, Job
from backend.scraper._shared.dedup import make_content_hash, make_external_id
from backend.tests.test_same_role import BODY


def _job(db, url, company="Twitch", title="Sr. Product Manager, Commerce", source="jobright",
         status="new", apply_url=None, days_old=0, commit=True, description=None, location=None):
    job = Job(external_id=make_external_id(company, title, url), content_hash=make_content_hash(company, title),
              company=company, title=title, url=url, apply_url=apply_url, source=source, status=status,
              seen=False, saved=status == "saved", description=description, location=location)
    if days_old:
        job.discovered_at = datetime.now(timezone.utc) - timedelta(days=days_old)
    db.add(job)
    if commit:
        db.commit()
    return job


def test_two_spellings_of_one_greenhouse_posting_are_one_job(test_db):
    first = _job(test_db, "https://job-boards.greenhouse.io/twitch/jobs/8686811002", source="levels_fyi")
    again = _job(test_db, "https://boards.greenhouse.io/embed/job_app?token=8686811002&utm_source=jobright")
    other = _job(test_db, "https://boards.greenhouse.io/embed/job_app?token=8686812002&utm_source=jobright")

    assert first.posting_key == again.posting_key == "greenhouse:8686811002"
    assert again.status == "duplicate" and again.duplicate_of == first.id
    assert first.status == "new" and first.duplicate_of is None
    assert other.status == "new", "a different posting id is a different job"


def test_copies_in_one_flush_link_to_the_first(test_db):
    a = _job(test_db, "https://jobs.citi.com/job/new-york/ach-product-lead/287/96335791200", commit=False)
    b = _job(test_db, "https://jobs.citi.com/job/-/-/287/96335791200?source=APPLICANT_SOURCE-3-354", commit=False)
    test_db.commit()
    assert b.status == "duplicate" and b.duplicate_of == a.id


def test_a_listing_with_the_postings_text_is_its_copy(test_db):
    """Rule 2: a LinkedIn listing, same company + title and the same text as an employer posting."""
    posting = _job(test_db, "https://jobs.ashbyhq.com/airwallex/1965a683-76f3-430a-9eb5-68456de0f121",
                   company="Airwallex", title="Senior Product Manager", source="direct", description=BODY)
    listing = _job(test_db, "https://www.linkedin.com/jobs/view/senior-product-manager-at-airwallex-4445868057",
                   company="Airwallex", title="Senior Product Manager", source="levels_fyi", description=BODY)
    assert listing.posting_key == "linkedin:4445868057"
    assert listing.status == "duplicate" and listing.duplicate_of == posting.id


def test_a_title_alone_proves_nothing(test_db):
    """Adobe: a LinkedIn "Senior Product Manager" without a text is not the Workday one; nor is a
    listing whose text differs (Capital One's two "Business Manager - US Card" roles)."""
    _job(test_db, "https://adobe.wd5.myworkdayjobs.com/x/job/San-Jose/Senior-Product-Manager_R170469-1",
         company="Adobe", title="Senior Product Manager", source="direct", description=BODY)
    bare = _job(test_db, "https://www.linkedin.com/jobs/view/4453748020", company="Adobe",
                title="Senior Product Manager", source="jobspy_linkedin")
    other = _job(test_db, "https://www.linkedin.com/jobs/view/4453748021", company="Adobe",
                 title="Senior Product Manager", source="levels_fyi",
                 description=BODY.replace("card issuing", "merchant onboarding"))
    assert bare.status == "new" and other.status == "new"


def test_a_role_is_not_matched_to_an_old_posting(test_db):
    _job(test_db, "https://jobs.lever.co/acme/1cafb489-9cae-4417-b272-832617484de0",
         company="Acme", title="PM", days_old=70, description=BODY)
    listing = _job(test_db, "https://www.linkedin.com/jobs/view/4461186967", company="Acme", title="PM",
                   description=BODY)
    assert listing.status == "new"


def test_one_role_listed_per_city_is_one_job(test_db):
    """Wells Fargo, Axon, Brex: the same text under a new id per city; and Amazon's several openings."""
    first = _job(test_db, "https://job-boards.greenhouse.io/axon/jobs/7805142003", company="Axon",
                 title="Senior Product Manager", location="Seattle, Washington, United States",
                 description=BODY + " Ref R-482211-8. Seattle, Washington. $205,200.")
    second = _job(test_db, "https://job-boards.greenhouse.io/axon/jobs/7805135003", company="Axon",
                  title="Senior Product Manager", location="New York, New York, United States",
                  description=BODY + " Ref R-482211-3. New York, New York. $171,000.")
    assert second.status == "duplicate" and second.duplicate_of == first.id


def test_one_other_word_keeps_two_roles(test_db):
    """Apple's Employee Experience PMs differ only in the area they name; Cisco's in the years asked."""
    a = _job(test_db, "https://jobs.apple.com/en-us/details/200677442-0670/x", company="Apple", title="PM",
             description=BODY.replace("card issuing", "legal tooling"))
    b = _job(test_db, "https://jobs.apple.com/en-us/details/200677429-0670/x", company="Apple", title="PM",
             description=BODY.replace("card issuing", "music tooling"))
    c = _job(test_db, "https://jobs.apple.com/en-us/details/200677386-0670/x", company="Apple", title="PM",
             description=BODY.replace("card issuing", "legal tooling").replace("5+ years", "8+ years"))
    assert a.status == b.status == c.status == "new"


def test_a_company_template_is_not_proof(test_db):
    """Sierra uses one text for its Spanish- and Portuguese-speaking strategists, so the text tells
    nothing about two postings with one of those titles either."""
    _job(test_db, "https://jobs.ashbyhq.com/Sierra/83a88812-d488-4a24-bb39-e1b0aa6dd240", company="Sierra",
         title="Strategist (Spanish speaking)", description=BODY)
    a = _job(test_db, "https://jobs.ashbyhq.com/Sierra/d7febb53-07d2-44fb-8cd0-7e5a011fdf24", company="Sierra",
             title="Strategist (Portuguese speaking)", description=BODY)
    b = _job(test_db, "https://jobs.ashbyhq.com/Sierra/66165ead-ae40-420a-93ff-aa6a5864462a", company="Sierra",
             title="Strategist (Portuguese speaking)", description=BODY)
    assert a.status == b.status == "new"


def test_a_role_in_one_flush_links_to_the_first(test_db):
    a = _job(test_db, "https://www.amazon.jobs/en/jobs/10566262/x", company="Amazon", title="Sr. PM",
             description=BODY, commit=False)
    b = _job(test_db, "https://www.amazon.jobs/en/jobs/10566263/x", company="Amazon", title="Sr. PM",
             description=BODY, commit=False)
    test_db.commit()
    assert b.status == "duplicate" and b.duplicate_of == a.id


def test_two_employer_postings_with_one_title_stay_two_jobs(test_db):
    """Adobe lists the same title under many requisitions; only the key says they are one."""
    a = _job(test_db, "https://adobe.wd5.myworkdayjobs.com/x/job/San-Jose/Senior-Product-Manager_R171155",
             company="Adobe", title="Senior Product Manager", source="direct")
    b = _job(test_db, "https://adobe.wd5.myworkdayjobs.com/x/job/San-Jose/Senior-Product-Manager_R167610",
             company="Adobe", title="Senior Product Manager", source="direct")
    assert a.status == b.status == "new"


def test_the_employer_posting_takes_over_an_untouched_listing(test_db):
    """Rule 3: the listing came first; when the employer's posting arrives, the listing is the copy."""
    listing = _job(test_db, "https://www.linkedin.com/jobs/view/4461186967", company="Acme", title="PM",
                   source="levels_fyi", description=BODY)
    bare = _job(test_db, "https://www.linkedin.com/jobs/view/4461186968", company="Acme", title="PM",
                source="jobspy_linkedin")
    posting = _job(test_db, "https://job-boards.greenhouse.io/acme/jobs/4012345", company="Acme", title="PM",
                   source="direct", description=BODY)
    test_db.refresh(bare)
    test_db.refresh(listing)
    assert bare.status == "new", "a listing without text is not proven a copy"
    assert posting.status == "new"
    assert listing.status == "duplicate" and listing.duplicate_of == posting.id


def test_a_saved_listing_keeps_the_role(test_db):
    """The user saved the Indeed listing: the employer's posting of that role is its copy, and a row
    created saved (the extension's save) is never hidden itself."""
    saved = _job(test_db, "https://www.indeed.com/viewjob?jk=0362e90102d8ac2c", company="Acme", title="PM",
                 source="jobspy_indeed", status="saved", description=BODY)
    again = _job(test_db, "https://www.linkedin.com/jobs/view/4461186967", company="Acme", title="PM",
                 source="extension", status="saved", description=BODY)
    posting = _job(test_db, "https://job-boards.greenhouse.io/acme/jobs/4012345", company="Acme", title="PM",
                   source="direct", description=BODY)
    test_db.refresh(saved)
    assert saved.status == "saved" and saved.duplicate_of is None
    assert again.status == "saved" and again.duplicate_of is None
    assert posting.status == "duplicate" and posting.duplicate_of == saved.id


def test_an_indeed_row_with_an_employer_link_joins_the_company_scrape(test_db):
    posting = _job(test_db, "https://job-boards.greenhouse.io/acme/jobs/4012345", company="Acme", title="PM",
                   source="direct")
    row = _job(test_db, "https://www.indeed.com/viewjob?jk=0362e90102d8ac2c", company="Acme Inc", title="PM",
               source="jobspy_indeed", apply_url="https://boards.greenhouse.io/acme/jobs/4012345?gh_src=x")
    assert row.posting_key == "greenhouse:4012345"
    assert row.duplicate_of == posting.id


def test_a_wrapped_link_gets_its_employer_link_and_key(test_db):
    job = _job(test_db, "https://ad.doubleclick.net/ddm/clk/464361346;269716711;m?"
                        "https://jobs.crateandbarrel.com/job/-/-/351/95352458448?utm_source=linkedin.com",
               company="Crate & Barrel", title="PM", source="linkedin_extension")
    assert job.posting_key == "talentbrew:351:95352458448"
    assert job.apply_url.startswith("https://jobs.crateandbarrel.com/job/-/-/351/95352458448")


def test_the_original_gets_the_employer_link_it_lacked(test_db):
    """The same Indeed listing again, now with the employer's link: it joins the listing, and the
    listing (saved, so it stays) takes the employer's link and key."""
    listing = _job(test_db, "https://www.indeed.com/viewjob?jk=0362e90102d8ac2c", company="Acme", title="PM",
                   source="jobspy_indeed", status="saved")
    again = _job(test_db, "https://www.indeed.com/viewjob?jk=0362E90102D8AC2C&vjs=3", company="Acme", title="PM",
                 source="jobspy_indeed", apply_url="https://jobs.lever.co/acme/1cafb489-9cae-4417-b272-832617484de0")
    test_db.refresh(listing)
    assert again.status == "duplicate" and again.duplicate_of == listing.id
    assert listing.status == "saved"
    assert listing.apply_url == "https://jobs.lever.co/acme/1cafb489-9cae-4417-b272-832617484de0"
    assert listing.posting_key == "lever:1cafb489-9cae-4417-b272-832617484de0"


def test_filtered_rows_are_neither_linked_nor_targets(test_db):
    ignored = _job(test_db, "https://job-boards.greenhouse.io/acme/jobs/4012345", status="ignored")
    visible = _job(test_db, "https://boards.greenhouse.io/acme/jobs/4012345?gh_src=x")
    assert ignored.posting_key == "greenhouse:4012345", "an ignored row is still keyed"
    assert visible.status == "new" and visible.duplicate_of is None


def test_another_accounts_posting_is_never_the_original(test_db):
    from backend.accounts.context import use_account
    _job(test_db, "https://job-boards.greenhouse.io/acme/jobs/4012345")
    partner = Account(name="Partner", role="member")
    test_db.add(partner)
    test_db.commit()
    with use_account(partner.id, test_db):
        theirs = _job(test_db, "https://boards.greenhouse.io/acme/jobs/4012345?gh_src=x")
        assert theirs.account_id == partner.id
        assert theirs.status == "new" and theirs.duplicate_of is None


def test_the_api_lists_where_else_a_posting_was_found(test_db, api_client):
    from backend.models.db import Setting
    test_db.add(Setting(key="dashboard_api_key", value=""))
    test_db.commit()
    first = _job(test_db, "https://job-boards.greenhouse.io/twitch/jobs/8686811002", source="levels_fyi")
    _job(test_db, "https://boards.greenhouse.io/embed/job_app?token=8686811002", source="jobright")

    body = api_client.get(f"/api/jobs/{first.id}").json()
    assert body["posting_key"] == "greenhouse:8686811002"
    assert body["also_on"] == [{"source": "jobright", "url": "https://boards.greenhouse.io/embed/job_app?token=8686811002",
                                "location": None}]
    listed = api_client.get("/api/jobs?status=new&limit=50").json()["jobs"]
    assert [j["id"] for j in listed] == [str(first.id)], "the feed hides the duplicate"
    assert listed[0]["also_on"][0]["source"] == "jobright"


def test_saving_another_spelling_from_the_extension_finds_the_posting(test_db, api_client):
    from backend.models.db import Setting
    test_db.add(Setting(key="dashboard_api_key", value=""))
    test_db.commit()
    first = _job(test_db, "https://job-boards.greenhouse.io/acme/jobs/4012345", company="Acme", title="PM")
    out = api_client.post("/api/jobs/save-from-extension", json={
        "title": "Product Manager", "company": "Acme Corp",
        "url": "https://careers.acme.com/open-roles?gh_jid=4012345"}).json()
    assert out["id"] == str(first.id) and out["new"] is False


def test_counts_as_new():
    from backend.scraper._shared.posting_links import counts_as_new
    assert counts_as_new(Job(status="new"))
    assert not counts_as_new(Job(status="duplicate"))


def test_backfill_keys_and_links_untouched_copies(test_db):
    from backend.seed import POSTING_KEYS_MIGRATED_KEY, migrate_posting_keys
    from backend.accounts.context import unscoped
    from backend.accounts.settings import install_row
    from backend.scraper._shared import posting_links

    # rows stored before 3.1: no keys, no linking (the listener is bypassed)
    saved_hook = posting_links.find_original
    posting_links.find_original = lambda *a, **k: None
    posting_links_listing = posting_links.listing_copies
    posting_links.listing_copies = lambda *a, **k: []
    try:
        old = _job(test_db, "https://job-boards.greenhouse.io/twitch/jobs/8686811002", days_old=5)
        applied = _job(test_db, "https://boards.greenhouse.io/embed/job_app?token=8686811002", status="applied")
        copy = _job(test_db, "https://job-boards.greenhouse.io/embed/job_app?for=twitch&token=8686811002")
    finally:
        posting_links.find_original = saved_hook
        posting_links.listing_copies = posting_links_listing
    for j in (old, applied, copy):
        j.posting_key = None
    test_db.commit()

    with unscoped():
        out = migrate_posting_keys(test_db)
        assert install_row(test_db, POSTING_KEYS_MIGRATED_KEY).value == "2"
        assert migrate_posting_keys(test_db) == {}, "runs once per reader version"
    assert out == {"rekeyed": 3, "unlinked": 0, "linked": 2}
    for j in (old, applied, copy):
        test_db.refresh(j)
    assert applied.status == "applied" and applied.duplicate_of is None, "the applied row keeps the posting"
    assert old.status == "duplicate" and old.duplicate_of == applied.id
    assert copy.status == "duplicate" and copy.duplicate_of == applied.id


def _stored(db, *jobs):
    """Store rows as an older version did: the listener's linking is bypassed."""
    from backend.scraper._shared import posting_links
    saved = posting_links.find_original, posting_links.listing_copies
    posting_links.find_original = lambda *a, **k: None
    posting_links.listing_copies = lambda *a, **k: []
    try:
        return [_job(db, *a, **k) for a, k in jobs]
    finally:
        posting_links.find_original, posting_links.listing_copies = saved


def test_backfill_v2_unlinks_title_only_copies_and_links_proven_roles(test_db):
    """3.1 linked a listing by title alone; 3.2 keeps that only with the same text, keys stored jobs
    again (Apple's role number, DocuSign's iCIMS id) and links untouched copies of one role."""
    from backend.accounts.context import unscoped
    from backend.models.db import Setting
    from backend.seed import POSTING_KEYS_MIGRATED_KEY, migrate_posting_keys
    posting, guess, nyc, cupertino, site, icims, per_city_a, per_city_b, acted = _stored(test_db,
        (("https://adobe.wd5.myworkdayjobs.com/x/job/San-Jose/Senior-Product-Manager_R170469-1",),
         dict(company="Adobe", title="SPM", description=BODY, days_old=3)),
        (("https://www.linkedin.com/jobs/view/4453748020",), dict(company="Adobe", title="SPM")),
        (("https://jobs.apple.com/en-us/details/200684524-2459/x",), dict(company="Apple", title="PPM", days_old=2)),
        (("https://jobs.apple.com/en-us/details/200684524-0836/x",), dict(company="Apple", title="PPM")),
        (("https://careers.docusign.com/jobs/30296",), dict(company="Docusign", title="GPM", days_old=2)),
        (("https://uscareers-docusign.icims.com/jobs/30296/x/job",), dict(company="Docusign", title="GPM")),
        (("https://www.linkedin.com/jobs/view/4464482250",),
         dict(company="Wells Fargo", title="LPM", description=BODY, location="Wilmington, DE", days_old=1)),
        (("https://www.linkedin.com/jobs/view/4464485175",),
         dict(company="Wells Fargo", title="LPM", description=BODY, location="Irving, TX")),
        (("https://www.linkedin.com/jobs/view/4464485176",),
         dict(company="Wells Fargo", title="LPM", description=BODY, location="Columbus, OH", status="skip")),
    )
    guess.status, guess.duplicate_of = "duplicate", posting.id  # the 3.1 title-only link
    nyc.posting_key, cupertino.posting_key = "apple:200684524-2459", "apple:200684524-0836"  # 3.1 keys
    site.posting_key = "site:careers.docusign.com:30296"
    test_db.query(Setting).filter(Setting.key == POSTING_KEYS_MIGRATED_KEY).delete()
    test_db.add(Setting(key=POSTING_KEYS_MIGRATED_KEY, value="1"))
    test_db.commit()

    with unscoped():
        out = migrate_posting_keys(test_db)
    assert out["unlinked"] == 1 and out["linked"] == 4
    for j in (guess, nyc, cupertino, site, icims, per_city_a, per_city_b, acted):
        test_db.refresh(j)
    assert guess.status == "new" and guess.duplicate_of is None, "a title alone proves nothing"
    assert nyc.posting_key == cupertino.posting_key == "apple:200684524"
    assert cupertino.duplicate_of == nyc.id
    assert site.posting_key == icims.posting_key == "icims:uscareers-docusign:30296"
    assert icims.duplicate_of == site.id
    assert acted.status == "skip" and acted.duplicate_of is None, "a row the user acted on is never moved"
    assert per_city_a.duplicate_of == per_city_b.duplicate_of == acted.id, "and keeps the role"


def test_backfill_keeps_an_employer_key_taken_over_from_a_copy(test_db):
    from backend.accounts.context import unscoped
    from backend.seed import migrate_posting_keys
    (listing,) = _stored(test_db, (("https://www.linkedin.com/jobs/view/4461186967",), dict(company="Acme", title="PM")))
    listing.posting_key = "greenhouse:4012345"
    test_db.commit()
    with unscoped():
        migrate_posting_keys(test_db)
    test_db.refresh(listing)
    assert listing.posting_key == "greenhouse:4012345"
