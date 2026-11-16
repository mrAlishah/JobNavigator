"""posting_key(): one posting, whatever link spelled it. URL shapes are the ones the feed holds."""
import pytest

from backend.scraper._shared.postings import (
    is_employer_key, is_short_link, posting_key, resolve_short_link, unwrap)


@pytest.mark.parametrize("urls, key", [
    # Greenhouse: hosted board, EU board, embed (two hosts), company pages with gh_jid
    ([
        "https://job-boards.greenhouse.io/twitch/jobs/8686811002",
        "https://boards.greenhouse.io/twitch/jobs/8686811002?gh_src=dd08aaff2us&source=LinkedIn",
        "https://job-boards.greenhouse.io/embed/job_app?for=twitch&token=8686811002",
        "https://boards.greenhouse.io/embed/job_app?token=8686811002&utm_source=jobright",
        "https://careers.twitch.com/?gh_jid=8686811002",
    ], "greenhouse:8686811002"),
    (["https://job-boards.eu.greenhouse.io/feverup/jobs/4678959101?gh_src=4a7b3162teu"], "greenhouse:4678959101"),
    (["https://jobs.elastic.co/jobs?gh_jid=6450040&gh_jid=6450040",
      "https://job-boards.greenhouse.io/elastic/jobs/6450040"], "greenhouse:6450040"),
    # Lever, Ashby (case folded), Rippling
    (["https://jobs.lever.co/spotify/1cafb489-9cae-4417-b272-832617484de0?utm_source=jobright",
      "https://jobs.lever.co/spotify/1CAFB489-9cae-4417-b272-832617484de0/apply"],
     "lever:1cafb489-9cae-4417-b272-832617484de0"),
    (["https://jobs.ashbyhq.com/airwallex/1965a683-76f3-430a-9eb5-68456de0f121?utm_source=jobright",
      "https://jobs.ashbyhq.com/airwallex/1965a683-76f3-430a-9eb5-68456de0f121/application"],
     "ashby:1965a683-76f3-430a-9eb5-68456de0f121"),
    (["https://ats.rippling.com/aerospike-inc/jobs/b272a1c7-54e9-4253-b5d7-0ae73311e313"],
     "rippling:b272a1c7-54e9-4253-b5d7-0ae73311e313"),
    # Workday: one requisition, a URL per location, with and without a locale
    ([
        "https://adobe.wd5.myworkdayjobs.com/external_experienced/job/San-Jose/Senior-Product-Manager_R170469-1",
        "https://adobe.wd5.myworkdayjobs.com/external_experienced/job/Remote-Ohio/Senior-Product-Manager_R170469-1",
        "https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced/job/San-Francisco/Senior-Product-Manager_R170469-1",
    ], "workday:adobe:R170469-1"),
    (["https://workday.wd5.myworkdayjobs.com/Workday/job/USA-MA-Boston/Sr-Evisort-Implementation-Manager--Engagement-Management_JR-0104540-1?source=website_l"],
     "workday:workday:JR-0104540-1"),
    # iCIMS: with and without the title slug
    (["https://careers-americas.icims.com/jobs/26289/job?utm_source=indeed_integration&iis=Job%20Board",
      "https://careers-americas.icims.com/jobs/26289/product-manager---rovo/job?iis=jobright&iisn=jobright"],
     "icims:careers-americas:26289"),
    # Oracle HCM: with and without a site segment
    (["https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/26010888",
      "https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/26010888",
      "https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/job/26010888/?utm_medium=jobboard"],
     "oracle:egug:26010888"),
    (["https://jobs.smartrecruiters.com/Achieve1/3743990014140816-lead-product-manager-lending-digital-experience-?oga=true"],
     "smartrecruiters:3743990014140816"),
    # TalentBrew: the slugs vary, the ids do not
    (["https://jobs.citi.com/job/new-york/ach-product-lead-us-domestic-payments-svp/287/96335791200",
      "https://jobs.citi.com/job/-/-/287/96335791200?source=APPLICANT_SOURCE-3-354&utm_medium=job_posting"],
     "talentbrew:287:96335791200"),
    (["https://paypal.eightfold.ai/careers/job/274908167038"], "eightfold:paypal:274908167038"),
    (["https://explore.jobs.netflix.net/careers/job/790314668577?microsite=netflix.com",
      "https://netflix.eightfold.ai/careers?pid=790314668577"], "eightfold:netflix:790314668577"),
    (["https://paypal.eightfold.ai/careers?pid=274922421537"], "eightfold:paypal:274922421537"),
    # hosted career platforms
    (["https://careers.ey.com/ey/job/Austin-Commercialization-Architect_Assistant-Director-TX-78701/1393883533/"],
     "successfactors:careers.ey.com:1393883533"),
    (["https://bloomberg.avature.net/careers/JobDetail/Applied-Data-Analyst-Data-Quality-Analytics/16808"],
     "avature:bloomberg.avature.net:16808"),
    (["https://careers.bain.com/jobs/FolderDetail/Expert-Manager-Product-Management-Innovation/107751"],
     "avature:careers.bain.com:107751"),
    (["https://kearney.taleo.net/careersection/01/jobdetail.ftl?job=006EY&tz=UTC-05%3A00"], "taleo:kearney:006EY"),
    (["https://jobs.gem.com/felix/am9icG9zdDoA1LwRA6RTyCK7nUKGeZr3"], "gem:am9icG9zdDoA1LwRA6RTyCK7nUKGeZr3"),
    # Avature's query form is the same posting as its path form; a board's tracking re-encoded into the id is dropped
    (["https://careers.ibm.com/careers/JobDetail?jobId=104594&source=WEB_Search_APAC"], "avature:careers.ibm.com:104594"),
    (["https://jobs.slalom.com/en_US/careersmarketplace/JobDetail?jobId=3866%3Fsource%3Djobright&src=jobright",
      "https://jobs.slalom.com/en_US/careersmarketplace/JobDetail/Senior-Architect-Solution-Ownership-Chicago/3866"],
     "avature:jobs.slalom.com:3866"),
    # first-party career sites
    (["https://careers.servicenow.com/jobs/744000104794717/principal-outbound-product-manager-app-engine/"],
     "site:careers.servicenow.com:744000104794717"),
    (["https://www.uber.com/careers/list/140922"], "site:uber.com:140922"),
    (["https://careers.cisco.com/global/en/job/2027633", "https://careers.cisco.com/global/en/job/2027633/Product-Manager"],
     "phenom:careers.cisco.com:2027633"),
    (["https://careers.adobe.com/us/en/job/ADOBUSR167610EXTERNALENUS/Senior-Product-Manager?utm_source=linkedin"],
     "phenom:careers.adobe.com:ADOBUSR167610EXTERNALENUS"),
    (["https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/job/CHARLOTTE-NC/Lead-Product-Manager_R-505105",
      "https://wf.wd1.myworkdayjobs.com/WellsFargoJobs/job/CHARLOTTE-NC/Lead-Product-Manager_R-505105"],
     "workday:wf:R-505105"),
    (["https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=ab12-CD&ccId=1&jobId=532614&lang=en_US"],
     "adp:ab12-cd:532614"),
    (["https://www.coinbase.com/careers/positions/6855533"], "site:coinbase.com:6855533"),
    # career sites that show an ATS posting under its ATS number
    (["https://careers.docusign.com/careers-home/jobs/30296", "https://careers.docusign.com/jobs/30296?lang=en-us",
      "https://uscareers-docusign.icims.com/jobs/30296/growth-product-manager/job?hub=9"], "icims:uscareers-docusign:30296"),
    (["https://careers.bankofamerica.com/en-us/job-detail/26031584/product-manager-tech-delivery-genai-new-york",
      "https://ghr.wd1.myworkdayjobs.com/lateral-us/job/New-York/Product-Manager---Tech-Delivery---GenAI_26031584"],
     "workday:ghr:26031584"),
    (["https://www.brex.com/careers/8436527002?department=Product", "https://www.brex.com/careers/8436527002?gh_jid=8436527002"],
     "greenhouse:8436527002"),
    (["https://stripe.com/careers/listing/abuse-investigator/8172487", "https://stripe.com/jobs/search?gh_jid=8172487"],
     "greenhouse:8172487"),
    (["https://joinbytedance.com/search/7463293367725787399"], "site:joinbytedance.com:7463293367725787399"),
    (["https://jobs.dayforcehcm.com/en-US/ranger/CANDIDATEPORTAL/jobs/1729"], "dayforce:ranger:1729"),
    (["https://recruiting.ultipro.com/NAT1014NGC/JobBoard/82e10fb7-67e7-49e3-ae40-66f508925bc6/OpportunityDetail?opportunityId=78ec111f-7a33-4b3e-a000-111122223333"],
     "ultipro:78ec111f-7a33-4b3e-a000-111122223333"),
    # first-party career sites
    (["https://www.amazon.jobs/de/jobs/3185436/senior-program-manager",
      "https://www.amazon.jobs/en/jobs/3185436/senior-program-manager"], "amazon:3185436"),
    # one Apple role per city: <role>-<location code>
    (["https://jobs.apple.com/en-us/details/200558938-0157/software-engineer?team=SFTWR",
      "https://jobs.apple.com/en-us/details/200558938-2459/software-engineer"], "apple:200558938"),
    (["https://www.google.com/about/careers/applications/jobs/results/100017909572477638-product-policy-lead"],
     "google:100017909572477638"),
    (["https://apply.careers.microsoft.com/careers/job/1970393556619498?domain=microsoft.com&hl=en"],
     "microsoft:1970393556619498"),
    (["https://www.metacareers.com/profile/job_details/1238249364564427"], "meta:1238249364564427"),
    # direct-links plan, Task 1: more systems and company sites
    (["https://www.careers.ford.com/en/job/-/-/48560/102309708864?utm_source=x",
      "https://www.careers.ford.com/job/-/-/48560/102309708864"], "talentbrew:48560:102309708864"),
    (["https://wd1.myworkdaysite.com/en-US/recruiting/snapchat/snap/job/Los-Angeles-California/Senior-Specialist_R0045568",
      "https://snapchat.wd1.myworkdayjobs.com/snap/job/Los-Angeles-California/Senior-Specialist_R0045568"],
     "workday:snapchat:R0045568"),
    (["https://careers.marsh.com/global/en/job/R_357136/Senior-Technology-Gen-AI-Delivery-Manager"],
     "phenom:careers.marsh.com:R_357136"),
    (["https://jobs.dayforcehcm.com/kiddeglobal/CANDIDATEPORTAL/jobs/3670?source=LinkedIn",
      "https://jobs.dayforcehcm.com/en-US/kiddeglobal/CANDIDATEPORTAL/jobs/3670"], "dayforce:kiddeglobal:3670"),
    (["https://bloomberg.avature.net/careers/jobdetail/private-markets-team-lead/21679",
      "https://bloomberg.avature.net/careers/JobDetail/Private-Markets-Team-Lead/21679"],
     "avature:bloomberg.avature.net:21679"),
    (["https://careers.bain.com/jobs/FolderDetail?folderId=102758&source=Linked-In",
      "https://careers.bain.com/jobs/FolderDetail/Lead-Product-Management/102758"], "avature:careers.bain.com:102758"),
    (["https://apply.deloitte.com/en_US/careers/InviteToApply?jobId=356989&source=Indeed",
      "https://apply.deloitte.com/en_US/careers/JobDetail?jobId=356989"], "avature:apply.deloitte.com:356989"),
    (["https://validity.applytojob.com/apply/J8VQtTi7Gp/Sr-Product-Manager-AI"], "jazzhr:J8VQtTi7Gp"),
    (["https://githubinc.jibeapply.com/jobs/5295?lang=en-us", "https://www.github.careers/careers-home/jobs/5295"],
     "jibe:githubinc:5295"),
    (["https://homedepot.contacthr.com/152256322"], "contacthr:homedepot:152256322"),
    (["https://careers.americanexpress.com/en/sites/CX_1/jobs/preview/26003740/?mode=location"],
     "oracle:careers.americanexpress.com:26003740"),
    (["https://corporate.visa.com/en/jobs/REF081042W",
      "https://corporate.visa.com/en-US/Visa/job/US---Austin-TX/Sr-Product-Manager_REF081042W",
      "https://visa.wd5.myworkdayjobs.com/en-US/Visa/job/US---Foster-City-CA/Sr-Product-Manager_REF081042W"],
     "workday:visa:REF081042W"),
    (["https://www.revolut.com/careers/position/04bf0992-3b62-48c4-a20c-aa744c8ad6e1/?source=Indeed"],
     "site:revolut.com:04bf0992-3b62-48c4-a20c-aa744c8ad6e1"),
    (["https://www.capgemini.com/jobs/497211-en_US_SAPBTP/Business-Advisor"], "site:capgemini.com:497211"),
    (["https://ibegin.tcsapps.com/candidate/jobs/417823J"], "site:ibegin.tcsapps.com:417823J"),
    # job boards: the listing, not the employer
    (["https://www.linkedin.com/jobs/view/4461186967",
      "https://www.linkedin.com/jobs/view/principal-product-manager-at-lawnstarter-4461186967",
      "https://www.linkedin.com/jobs/search/?currentJobId=4461186967&keywords=pm"], "linkedin:4461186967"),
    (["https://www.indeed.com/viewjob?jk=0362e90102d8ac2c", "https://uk.indeed.com/viewjob?jk=0362E90102D8AC2C&vjs=3"],
     "indeed:0362e90102d8ac2c"),
])
def test_every_spelling_gives_one_key(urls, key):
    assert {posting_key(u) for u in urls} == {key}


def test_different_postings_keep_different_keys():
    assert posting_key("https://job-boards.greenhouse.io/twitch/jobs/8686811002") != \
        posting_key("https://job-boards.greenhouse.io/twitch/jobs/8686812002")
    assert posting_key("https://adobe.wd5.myworkdayjobs.com/x/job/San-Jose/Senior-Product-Manager_R171155") != \
        posting_key("https://adobe.wd5.myworkdayjobs.com/x/job/San-Jose/Senior-Product-Manager_R167610")
    # two Apple roles in one city
    assert posting_key("https://jobs.apple.com/en-us/details/200677442-0670/x") != \
        posting_key("https://jobs.apple.com/en-us/details/200677429-0670/x")
    # same requisition number at two Workday tenants is two postings
    assert posting_key("https://a.wd1.myworkdayjobs.com/s/job/X/T_R100") != \
        posting_key("https://b.wd1.myworkdayjobs.com/s/job/X/T_R100")


@pytest.mark.parametrize("url", [
    None, "", "not a url", "mailto:jobs@example.com",
    "https://cursor.com/careers/account-associate",            # unknown system: no guess
    "https://stripe.com/careers/compatibility",
    "https://www.brex.com/careers",
    "https://careers.bankofamerica.com/en-us/job-search",
    "https://www.uber.com/en/jobs/saved-jobs/",
    "https://careers.bain.com/jobs/FolderDetail",
    "https://homedepot.contacthr.com/",
    "https://job-boards.greenhouse.io/twitch",                  # a board, not a job
    "https://adobe.wd5.myworkdayjobs.com/external_experienced", # a site, not a job
    "https://www.linkedin.com/jobs/search/?keywords=pm",
    "https://www.ziprecruiter.com/ekn/AAJ_ADplQyx_o3QL3x5ENufsITOKbiy",
])
def test_unknown_or_non_posting_links_name_nothing(url):
    assert posting_key(url) is None


def test_click_tracker_is_unwrapped_without_a_request():
    wrapped = ("https://ad.doubleclick.net/ddm/clk/464361346;269716711;m?"
               "https://jobs.crateandbarrel.com/job/-/-/351/95352458448?utm_source=linkedin.com&utm_campaign=x")
    assert unwrap(wrapped).startswith("https://jobs.crateandbarrel.com/job/-/-/351/95352458448")
    assert posting_key(wrapped) == "talentbrew:351:95352458448"
    encoded = ("https://ad.doubleclick.net/ddm/trackclk/N4789.466581LINKEDIN.COM/B319;dc_trk_aid=6;"
               "dc_lat=;dc_rdid=;tag_for_child_directed_treatment=;tfua=?"
               "https%3A%2F%2Fjob-boards.greenhouse.io%2Facme%2Fjobs%2F4012345")
    assert posting_key(encoded) == "greenhouse:4012345"
    assert unwrap("https://jobs.lever.co/a/b") == "https://jobs.lever.co/a/b"


def test_employer_keys_and_board_keys():
    assert is_employer_key("greenhouse:1")
    assert not is_employer_key("linkedin:1")
    assert not is_employer_key("indeed:abc")
    assert not is_employer_key(None)


def test_short_links():
    assert is_short_link("https://grnh.se/0dr0uu8u1us")
    assert is_short_link("https://click.appcast.io/t/437Sjcd49BJ_DmgMYWc0aCOCdXfjYUg_HUeufcxG96g=")
    assert is_short_link("https://dsp.prng.co/GTkkPyb")
    assert not is_short_link("https://boards.greenhouse.io/x/jobs/1")


@pytest.mark.asyncio
async def test_resolve_short_link_follows_to_the_posting(monkeypatch):
    class _Resp:
        url = "https://boards.greenhouse.io/acme/jobs/4012345?gh_src=x"

    async def fake_get(url, timeout=None):
        assert url == "https://grnh.se/abc123"
        return _Resp()

    monkeypatch.setattr("backend.scraper._shared.url_safety.safe_get", fake_get)
    assert await resolve_short_link("https://grnh.se/abc123") == _Resp.url
    # anything else is left alone, without a request
    assert await resolve_short_link("https://jobs.lever.co/a/b") == "https://jobs.lever.co/a/b"


@pytest.mark.asyncio
async def test_resolve_short_link_keeps_the_link_on_failure(monkeypatch):
    async def boom(url, timeout=None):
        raise RuntimeError("down")

    monkeypatch.setattr("backend.scraper._shared.url_safety.safe_get", boom)
    assert await resolve_short_link("https://grnh.se/abc123") == "https://grnh.se/abc123"
