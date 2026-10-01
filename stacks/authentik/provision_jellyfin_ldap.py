"""Provision the Jellyfin LDAP outpost in authentik. Idempotent.

Run it inside the server container:

    docker cp stacks/authentik/provision_jellyfin_ldap.py authentik-server:/tmp/p.py
    docker exec authentik-server ak shell -c 'exec(open("/tmp/p.py").read())'

It prints the outpost token and bind password ONLY when it creates them.
Everything it configures lives in Postgres, so without this script a rebuilt
authentik loses the entire Jellyfin integration with no record of how it was
put together.

Four things here are not obvious and were each found by a failed bind:

1. The bind account must be `service_account`, NOT `internal_service_account`.
   The latter is reserved for authentik's own outposts and the authentication
   flow refuses it with "Flow does not apply to current user."

2. The provider's authorization_flow cannot be the stock
   `default-provider-authorization-implicit-consent`. That flow is
   `require_authenticated`, and an LDAP bind has no session yet.

3. That replacement flow must contain real authentication stages. With no
   stages it authorizes nobody: `check_access` evaluates
   `PolicyEngine(app, request.user, request)`, and with no UserLoginStage
   there is no session, so request.user is anonymous and every bind fails
   with "Insufficient access". MFA is deliberately excluded -- an LDAP bind
   cannot prompt for a second factor.

4. Object permissions attach to Roles, not users (authentik 2026+). The bind
   account needs `search_full_directory` via a role on a group, or it binds
   successfully and then sees an empty directory.
"""

import secrets

from guardian.shortcuts import assign_perm

from authentik.core.models import Application, Group, User, UserTypes
from authentik.flows.models import (
    Flow,
    FlowAuthenticationRequirement,
    FlowDesignation,
    FlowStageBinding,
    Stage,
)
from authentik.outposts.models import Outpost, OutpostType
from authentik.policies.models import PolicyBinding
from authentik.providers.ldap.models import LDAPProvider
from authentik.rbac.models import Role

BASE_DN = "dc=ldap,dc=goauthentik,dc=io"
SECRETS = {}


def note(msg):
    print(f"  {msg}")


# --- access group: membership here is the on/off switch for Jellyfin --------
access_group, created = Group.objects.get_or_create(name="jellyfin-users")
note(f"group jellyfin-users: {'created' if created else 'existed'}")

# --- bind account ----------------------------------------------------------
svc, created = User.objects.get_or_create(
    username="svc-jellyfin-ldap",
    defaults={"name": "Jellyfin LDAP bind", "type": UserTypes.SERVICE_ACCOUNT},
)
if svc.type != UserTypes.SERVICE_ACCOUNT:
    svc.type = UserTypes.SERVICE_ACCOUNT
    svc.save()
    note("svc-jellyfin-ldap: corrected user type to service_account")
if created:
    pw = secrets.token_urlsafe(32)
    svc.set_password(pw)
    svc.save()
    SECRETS["JELLYFIN_LDAP_BIND_PASSWORD"] = pw
note(f"svc-jellyfin-ldap: {'created' if created else 'existed'}")

# --- bind flow -------------------------------------------------------------
bind_flow, created = Flow.objects.get_or_create(
    slug="ldap-authorization-flow",
    defaults={
        "name": "LDAP Authorization",
        "title": "Authorizing LDAP bind",
        "designation": FlowDesignation.AUTHORIZATION,
        "authentication": FlowAuthenticationRequirement.NONE,
    },
)
note(f"flow ldap-authorization-flow: {'created' if created else 'existed'}")

for order, stage_name in (
    (10, "default-authentication-identification"),
    (20, "default-authentication-password"),
    (100, "default-authentication-login"),
):
    binding, made = FlowStageBinding.objects.get_or_create(
        target=bind_flow, stage=Stage.objects.get(name=stage_name), defaults={"order": order}
    )
    if binding.order != order:
        binding.order = order
        binding.save()
note(f"bind flow stages: {FlowStageBinding.objects.filter(target=bind_flow).count()}")

# --- provider --------------------------------------------------------------
provider, created = LDAPProvider.objects.get_or_create(
    name="jellyfin-ldap",
    defaults={
        "authentication_flow": Flow.objects.get(slug="default-authentication-flow"),
        "authorization_flow": bind_flow,
        "invalidation_flow": Flow.objects.get(slug="default-provider-invalidation-flow"),
        "base_dn": BASE_DN,
    },
)
if provider.authorization_flow_id != bind_flow.pk:
    provider.authorization_flow = bind_flow
    provider.save()
    note("provider: corrected authorization_flow")
note(f"provider jellyfin-ldap: {'created' if created else 'existed'} base_dn={provider.base_dn}")

# --- application, and the policy that actually gates access ----------------
app, created = Application.objects.get_or_create(
    slug="jellyfin", defaults={"name": "Jellyfin", "provider": provider}
)
if app.provider_id != provider.pk:
    app.provider = provider
    app.save()
note(f"application jellyfin: {'created' if created else 'existed'}")

_, made = PolicyBinding.objects.get_or_create(
    target=app, group=access_group, defaults={"order": 0, "enabled": True}
)
note(f"policy: jellyfin-users -> app {'created' if made else 'existed'}")

_, made = PolicyBinding.objects.get_or_create(
    target=app, user=svc, defaults={"order": 10, "enabled": True}
)
note(f"policy: bind account -> app {'created' if made else 'existed'}")

# --- search permission, via a role -----------------------------------------
role, created = Role.objects.get_or_create(name="jellyfin-ldap-search")
assign_perm("authentik_providers_ldap.search_full_directory", role, provider)
assign_perm("authentik_providers_ldap.search_full_directory", role)
svc_group, _ = Group.objects.get_or_create(name="jellyfin-ldap-service")
svc_group.roles.add(role)
svc_group.users.add(svc)
svc = User.objects.get(username="svc-jellyfin-ldap")
note(f"search permission: obj={svc.has_perm('search_full_directory', provider)} "
     f"global={svc.has_perm('authentik_providers_ldap.search_full_directory')}")

# --- outpost ---------------------------------------------------------------
# service_connection=None is integration "none": authentik must NOT spawn a
# container. Ours is declared in compose.yaml.
outpost, created = Outpost.objects.get_or_create(
    name="jellyfin-ldap-outpost",
    defaults={"type": OutpostType.LDAP, "service_connection": None},
)
outpost.providers.set([provider])
outpost.save()
note(f"outpost jellyfin-ldap-outpost: {'created' if created else 'existed'} "
     f"integration={outpost.service_connection}")
if created:
    SECRETS["AUTHENTIK_LDAP_TOKEN"] = outpost.token.key

if SECRETS:
    print("\nWrite these into stacks/authentik/.env on the server:")
    for key, value in SECRETS.items():
        print(f"{key}={value}")
else:
    print("\nNo new secrets: everything already existed.")
