"""
github_util
"""

import datetime
import os
import logging

import github
import github.GithubException
import github.Issue
import github.Repository
import github.PaginatedList

import snapshot_manager.config as config
import snapshot_manager.build_status as build_status


class GithubClient:
    def __init__(self, config: config.Config, github_token: str = None, **kwargs):
        """
        Keyword Arguments:
            github_token (str, optional): github personal access token.
        """
        self.config = config
        if github_token is None:
            github_token = os.getenv(self.config.github_token_env)
        self.github = github.Github(login_or_token=github_token)
        self.__label_cache = None
        self.__repo_cache = None

    @property
    def gh_repo(self) -> github.Repository.Repository:
        if self.__repo_cache is None:
            self.__repo_cache = self.github.get_repo(self.config.github_repo)
        return self.__repo_cache

    def get_todays_github_issue(
        self,
        strategy: str,
        creator: str = "github-actions[bot]",
        github_repo: str | None = None,
    ) -> github.Issue.Issue | None:
        """Returns the github issue (if any) for today's snapshot that was build with the given strategy.

        If no issue was found, `None` is returned.

        Args:
            strategy (str): The build strategy to pick (e.g. "standalone", "big-merge").
            creator (str|None, optional): The author who should have created the issue. Defaults to github-actions[bot]
            repo (str|None, optional): The repo to use. This is only useful for testing purposes. Defaults to None which will result in whatever the github_repo property has.

        Raises:
            ValueError if the strategy is empty

        Returns:
            github.Issue.Issue|None: The found issue or None.
        """
        if not strategy:
            raise ValueError("parameter 'strategy' must not be empty")

        if github_repo is None:
            github_repo = self.config.github_repo

        # See https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests
        # label:broken_snapshot_detected
        query = f"is:issue repo:{github_repo} author:{creator} label:strategy/{strategy} {self.config.yyyymmdd} in:title"
        issues = self.github.search_issues(query)
        if issues is not None and issues.totalCount > 0:
            logging.info(f"Found today's issue: {issues[0].html_url}")
            return issues[0]
        logging.info("Found no issue for today")
        return None

    @property
    def initial_comment(self) -> str:
        return f"""
Hello @{self.config.maintainer_handle}!

<p>
This issue exists to let you know that we are about to monitor the builds
of the LLVM snapshot for <a href="{self.config.copr_monitor_url}">{self.config.yyyymmdd}</a>.
<details>
<summary>At certain intervals the CI system will update this very comment over time to reflect the progress of builds.</summary>
<dl>
<dt>Log analysis</dt>
<dd>For example if a build of the <code>llvm</code> project fails on the <code>fedora-rawhide-x86_64</code> platform,
we'll analyze the build log (if any) to identify the cause of the failure. The cause can be any of <code>{build_status.ErrorCause.list()}</code>.
For each cause we will list the packages and the relevant log excerpts.</dd>
<dt>Use of labels</dt>
<dd>Let's assume a unit test test in upstream LLVM was broken.
We will then add these labels to this issue: <code>error/test</code>, <code>arch/x86_64</code>, <code>os/fedora-rawhide</code>, <code>project/llvm</code>.
If you manually restart a build in Copr and can bring it to a successful state, we will automatically
remove the aforementioned labels.
</dd>
</dl>
</details>
</p>

{self.config.update_marker}

<p><b>Last updated: {datetime.datetime.now().isoformat()}</b></p>
"""

    def create_or_get_todays_github_issue(
        self,
        maintainer_handle: str,
        creator: str = "github-actions[bot]",
    ) -> tuple[github.Issue.Issue, bool]:
        issue = self.get_todays_github_issue(
            strategy=self.config.build_strategy,
            creator=creator,
            github_repo=self.config.github_repo,
        )
        if issue is not None:
            return (issue, False)

        strategy = self.config.build_strategy
        repo = self.gh_repo
        logging.info("Creating issue for today")
        issue = repo.create_issue(
            assignee=maintainer_handle,
            title=f"Snapshot build for {self.config.yyyymmdd} ({strategy})",
            body=self.initial_comment,
        )
        self.create_labels_for_strategies(labels=[strategy])
        issue.add_to_labels(f"strategy/{strategy}")
        return (issue, True)

    @property
    def label_cache(self, refresh: bool = False) -> github.PaginatedList.PaginatedList:
        """Will query the labels of a github repo only once and return it afterwards.

        Args:
            refresh (bool, optional): The cache will be emptied. Defaults to False.

        Returns:
            github.PaginatedList.PaginatedList: An enumerable list of github.Label.Label objects
        """
        if self.__label_cache is None or refresh:
            self.__label_cache = self.gh_repo.get_labels()
        return self.__label_cache

    def is_label_in_cache(self, name: str, color: str) -> bool:
        """Returns True if the label exists in the cache.

        Args:
            name (str): Name of the label to look for
            color (str): Color string of the label to look for

        Returns:
            bool: True if the label is in the cache
        """
        for label in self.label_cache:
            if label.name == name and label.color == color:
                return True
        return False

    def _create_labels(
        self,
        prefix: str,
        color: str,
        labels: list[str] = [],
    ):
        """Iterates over the given labels and creates or edits each label in the list
        with the given prefix and color."""
        if labels is None or len(labels) == 0:
            return

        labels = set(labels)
        labels = list(labels)
        labels.sort()
        for label in labels:
            labelname = label
            if not labelname.startswith(prefix):
                labelname = f"{prefix}{label}"
            if self.is_label_in_cache(name=labelname, color=color):
                continue
            logging.info(
                f"Creating label: repo={self.config.github_repo} name={labelname} color={color}",
            )
            try:
                self.gh_repo.create_label(color=color, name=labelname)
            except:
                self.gh_repo.get_label(name=labelname).edit(
                    name=labelname, color=color, description=""
                )

    def _get_labels_on_issue(self, issue: github.Issue.Issue, prefix: str) -> list[str]:
        return [
            label.name for label in issue.get_labels() if label.name.startswith(prefix)
        ]

    def get_error_labels_on_issue(self, issue: github.Issue.Issue) -> list[str]:
        return self._get_labels_on_issue(issue, prefix="error/")

    def get_os_labels_on_issue(self, issue: github.Issue.Issue) -> list[str]:
        return self._get_labels_on_issue(issue, prefix="os/")

    def get_arch_labels_on_issue(self, issue: github.Issue.Issue) -> list[str]:
        return self._get_labels_on_issue(issue, prefix="arch/")

    def get_project_labels_on_issue(self, issue: github.Issue.Issue) -> list[str]:
        return self._get_labels_on_issue(issue, prefix="project/")

    def create_labels_for_error_causes(self, labels: list[str], **kw_args):
        self._create_labels(labels=labels, prefix="error/", color="FBCA04", **kw_args)

    def create_labels_for_oses(self, labels: list[str], **kw_args):
        self._create_labels(labels=labels, prefix="os/", color="F9D0C4", **kw_args)

    def create_labels_for_projects(self, labels: list[str], **kw_args):
        self._create_labels(labels=labels, prefix="project/", color="BFDADC", **kw_args)

    def create_labels_for_strategies(self, labels: list[str], **kw_args):
        self._create_labels(labels=labels, prefix="strategy/", color="FFFFFF", *kw_args)

    def create_labels_for_archs(self, labels: list[str], **kw_args):
        self._create_labels(labels=labels, prefix="arch/", color="C5DEF5", *kw_args)

    def create_labels_for_in_testing(self, labels: list[str], **kw_args):
        self._create_labels(
            labels=labels, prefix="in_testing/", color="FEF2C0", *kw_args
        )

    def create_labels_for_tested_on(self, labels: list[str], **kw_args):
        self._create_labels(
            labels=labels, prefix="tested_on/", color="0E8A16", *kw_args
        )

    def create_labels_for_failed_on(self, labels: list[str], **kw_args):
        self._create_labels(
            labels=labels, prefix="failed_on/", color="D93F0B", *kw_args
        )