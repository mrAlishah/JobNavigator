// JobSpy boards that refuse this scraper on every measured run. They stay
// selectable and stay in the default source lists; the badge only says so.
//
// The backend maps the same two blocks to their run error text in
// backend/scraper/sources/jobspy.py (`_KNOWN_BLOCKS`). Change both places together.
export const BLOCKED_BADGE = 'Often blocked'

export const SOURCE_BLOCKS = {
  zip_recruiter: 'The ZipRecruiter server refuses the requests that this scraper sends.',
  google: 'Google usually returns a JavaScript check page instead of job data, and this scraper cannot read that page.',
}
