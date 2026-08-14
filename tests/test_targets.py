"""The pre-prod host contains the production host, and that is the whole bug.

`run-itsm` failed six times without writing anything because the guard asked whether the production
hostname appeared anywhere in the URL, and `dev.provy.ai` contains `provy.ai`. The lifecycle
installer asked the same question and, on a `True`, rewrote the instance's `provy.ingest.url`.

Both directions matter here. A false positive blocks every pre-prod run and silently redirects a
correctly-configured ServiceNow instance. A false negative points a demo at production and writes
real outcomes into the real ledger, which happened on 2026-07-27.
"""

from engine.targets import is_production_target, target_host


class TestPreProdIsNotProduction:
    """The regression. Each of these was refused as production before the fix."""

    def test_custom_preprod_domain(self):
        assert not is_production_target("https://dev.provy.ai/api/ingest/outcome")

    def test_vercel_preprod_domain(self):
        assert not is_production_target("https://provydev.vercel.app/api/ingest/outcome")

    def test_any_subdomain_of_prod_is_not_prod(self):
        assert not is_production_target("https://staging.provy.ai/api/ingest/outcome")


class TestProductionIsStillCaught:
    """The guard exists for these. Losing them is worse than the bug it replaced."""

    def test_bare_prod_domain(self):
        assert is_production_target("https://provy.ai/api/ingest/outcome")

    def test_prod_vercel_alias(self):
        assert is_production_target("https://provyai.vercel.app/api/ingest/outcome")

    def test_case_and_port_do_not_hide_it(self):
        assert is_production_target("HTTPS://PROVY.AI:443/api/ingest/outcome")

    def test_host_without_a_scheme(self):
        # A value copied out of a config without its scheme still has to be judged. urlparse leaves
        # `hostname` as None here, which would read as "not production" if it were trusted directly.
        assert is_production_target("provy.ai/api/ingest/outcome")


class TestNothingIsSilentlySafe:
    def test_empty_is_not_production_but_is_also_not_a_host(self):
        assert target_host("") == ""
        assert not is_production_target("")

    def test_a_lookalike_domain_is_not_our_production(self):
        # Someone else's domain that merely ends in the same string is not our ledger.
        assert not is_production_target("https://notprovy.ai/api/ingest/outcome")
