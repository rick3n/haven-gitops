Once per month we update HavenPlus components to new versions, based on MR's that Renovate Bot created. These components are verified against a specific Kubernetes version. 

# Prep
- Read release notes for all components for which [Renovate has openend an MR](https://gitlab.com/commonground/haven/havenplus/gitops-flux/-/merge_requests/?sort=created_date&state=opened&author_username=group_3094865_bot_11065d24d60249032aac86922bc492d6&first_page_size=20).

# Actions
- [ ] merge renovate MR's to the `main` branch
- [ ] verify changes on a local cluster (see verification list below)
- [ ] verify changes on the Cyso staging cluster (see verification list below)

# Verification list
<fill in the list below with HavenPlus components which need to be validated. This should be done based on the MRs that Renovate provided>
- [ ] ...
- [ ] ...
