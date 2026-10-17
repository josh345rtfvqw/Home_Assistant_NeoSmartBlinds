"""Tests for the config flow, blind subentries and YAML import."""

from __future__ import annotations

from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.neosmartblinds.const import (
    CONF_BLIND_CODE,
    CONF_CLOSE_TIME,
    CONF_HUB_ID,
    CONF_MOTOR_CODE,
    CONF_PARENT,
    CONF_PERCENT_SUPPORT,
    CONF_PROTOCOL,
    CONF_RAIL,
    CONF_START_POSITION,
    CONF_TDBU_EXTENDED,
    CONF_TILT,
    DOMAIN,
    SUBENTRY_BLIND,
)

from .conftest import (
    HUB_ID,
    FakeController,
    blind_subentry,
    controller_entry,
    setup_entry,
)


def _port_default(result) -> int:
    """Return the default shown in the port field."""
    for key in result["data_schema"].schema:
        if key == CONF_PORT:
            return key.default()
    raise AssertionError("no port field")


def _suggested(result, field: str):
    for key in result["data_schema"].schema:
        if key == field:
            return (key.description or {}).get("suggested_value")
    raise AssertionError(f"no {field} field")


async def test_user_flow(hass: HomeAssistant, fake_controller: FakeController) -> None:
    """Address and protocol first, then a port prefilled for the protocol."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: " 127.0.0.1 ", CONF_PROTOCOL: "http"}
    )
    assert result["step_id"] == "connection"
    assert _port_default(result) == 8838

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORT: 1, CONF_HUB_ID: HUB_ID}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PORT: fake_controller.http_port, CONF_HUB_ID: f" {HUB_ID} "},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_HOST: "127.0.0.1",
        CONF_PROTOCOL: "http",
        CONF_PORT: fake_controller.http_port,
        CONF_HUB_ID: HUB_ID,
    }
    assert result["result"].unique_id == HUB_ID
    # Checking the connection must not move anything
    assert fake_controller.received == []

    # Same controller again is rejected at the first step
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PROTOCOL: "tcp"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_flow_tcp(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """TCP prefills 8839 and doesn't need a controller id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PROTOCOL: "tcp"}
    )
    assert _port_default(result) == 8839
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORT: fake_controller.tcp_port}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HUB_ID] == ""
    assert result["result"].unique_id == "127.0.0.1"


async def test_reconfigure(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """The controller's settings can be changed; a new protocol offers its port."""
    entry = controller_entry(fake_controller, [blind_subentry()])
    await setup_entry(hass, entry)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PROTOCOL: "tcp"}
    )
    assert result["step_id"] == "reconfigure_connection"
    assert _suggested(result, CONF_PORT) == 8839
    assert _suggested(result, CONF_HUB_ID) == HUB_ID
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PORT: fake_controller.tcp_port, CONF_HUB_ID: HUB_ID},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_PROTOCOL] == "tcp"
    await hass.services.async_call(
        COVER_DOMAIN, "open_cover", {"entity_id": "cover.blind_one"}, blocking=True
    )
    assert fake_controller.received == ["021.230-04-up!bf"]


async def test_reauth(hass: HomeAssistant, fake_controller: FakeController) -> None:
    """Reauth replaces the hub id."""
    entry = controller_entry(fake_controller, [blind_subentry()])
    await setup_entry(hass, entry)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HUB_ID: "new-id"}
    )
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_HUB_ID] == "new-id"


BLIND_FORM = {
    CONF_NAME: "Kitchen",
    CONF_BLIND_CODE: "021.230-05",
    CONF_MOTOR_CODE: "no",
    CONF_RAIL: "1",
    CONF_TDBU_EXTENDED: False,
    CONF_PERCENT_SUPPORT: "1",
    CONF_CLOSE_TIME: 25,
    CONF_PARENT: "021.230-15",
}


async def test_add_blind(hass: HomeAssistant, fake_controller: FakeController) -> None:
    """Blinds are added as subentries, with validation."""
    entry = controller_entry(fake_controller, [blind_subentry()])
    await setup_entry(hass, entry)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_BLIND), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    bad = BLIND_FORM | {
        CONF_BLIND_CODE: "21.230-5",
        CONF_MOTOR_CODE: "bf",
        CONF_TDBU_EXTENDED: True,
        CONF_PARENT: "nope",
    }
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], bad
    )
    assert result["errors"] == {
        CONF_BLIND_CODE: "invalid_blind_code",
        CONF_TDBU_EXTENDED: "tdbu_extended_motor",
        CONF_PARENT: "invalid_blind_code",
        CONF_PERCENT_SUPPORT: "position_not_supported",
    }

    dup = BLIND_FORM | {CONF_BLIND_CODE: "021.230-04"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dup
    )
    assert result["errors"] == {"base": "already_configured"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], BLIND_FORM | {CONF_START_POSITION: 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    sub = next(s for s in entry.subentries.values() if s.title == "Kitchen")
    assert sub.unique_id == "neosmartblinds.021.230-05.no.1"
    assert sub.data == {
        CONF_BLIND_CODE: "021.230-05",
        CONF_MOTOR_CODE: "no",
        CONF_RAIL: 1,
        CONF_TDBU_EXTENDED: False,
        CONF_TILT: False,
        CONF_PERCENT_SUPPORT: 1,
        CONF_CLOSE_TIME: 25,
        CONF_START_POSITION: 0,
        CONF_PARENT: "021.230-15",
    }
    # The entry reloaded and the new blind is live
    assert hass.states.get("cover.kitchen").attributes["current_position"] == 0


async def test_reconfigure_blind(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """Changing a blind keeps its entity."""
    entry = controller_entry(fake_controller, [blind_subentry()])
    await setup_entry(hass, entry)
    subentry_id = next(iter(entry.subentries))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_BLIND),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Blind One",
            CONF_BLIND_CODE: "021.230-04",
            CONF_MOTOR_CODE: "no",
            CONF_RAIL: "1",
            CONF_TDBU_EXTENDED: False,
            CONF_PERCENT_SUPPORT: "1",
            CONF_CLOSE_TIME: 30,
        },
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries[subentry_id].data[CONF_MOTOR_CODE] == "no"
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    covers = [e for e in entities if e.domain == COVER_DOMAIN]
    assert [e.entity_id for e in covers] == ["cover.blind_one"]
    await hass.services.async_call(
        COVER_DOMAIN,
        "set_cover_position",
        {"entity_id": "cover.blind_one", "position": 20},
        blocking=True,
    )
    assert fake_controller.received == ["021.230-04-80!no"]


YAML_BLIND = {
    "platform": DOMAIN,
    "name": "Blind One",
    "host": "127.0.0.1",
    "hub_id": HUB_ID,
    "protocol": "HTTP",
    "blind_code": "021.230-04",
    "close_time": 30,
    "rail": 1,
    "percent_support": 2,
    "motor_code": "bf",
    "start_position": 50,
    "parent_group": "021.230-15",
}


async def test_yaml_import(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """YAML blinds are imported into one controller entry, keeping entity ids."""
    registry = er.async_get(hass)
    # An entity left behind by v2 (no config entry)
    old = registry.async_get_or_create(
        COVER_DOMAIN,
        DOMAIN,
        "neosmartblinds.021.230-04.bf.1",
        suggested_object_id="lounge_left",
    )
    second = YAML_BLIND | {
        "name": "Blind Two",
        "blind_code": "021.230-05",
        "port": fake_controller.http_port,
    }
    first = YAML_BLIND | {"port": fake_controller.http_port}
    del first["start_position"]
    assert await async_setup_component(
        hass, COVER_DOMAIN, {COVER_DOMAIN: [first, second]}
    )
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.unique_id == HUB_ID
    assert entry.data[CONF_PROTOCOL] == "http"
    assert sorted(s.title for s in entry.subentries.values()) == [
        "Blind One",
        "Blind Two",
    ]
    two = next(s for s in entry.subentries.values() if s.title == "Blind Two")
    assert two.data[CONF_START_POSITION] == 50
    assert two.data[CONF_PARENT] == "021.230-15"

    # The v2 entity is adopted with its entity id
    adopted = registry.async_get(old.entity_id)
    assert adopted.config_entry_id == entry.entry_id
    assert hass.states.get("cover.lounge_left") is not None
    assert hass.states.get("cover.blind_two") is not None

    issues = ir.async_get(hass)
    assert issues.async_get_issue(HOMEASSISTANT_DOMAIN, f"deprecated_yaml_{DOMAIN}")

    # Importing again on the next start doesn't duplicate anything
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "import"}, data=first
    )
    assert result["reason"] == "already_configured"
    assert len(entry.subentries) == 2


async def test_yaml_import_invalid(hass: HomeAssistant) -> None:
    """Bad YAML raises a repair issue instead of an entry."""
    bad = YAML_BLIND | {"blind_code": "21-4"}
    assert await async_setup_component(hass, COVER_DOMAIN, {COVER_DOMAIN: [bad]})
    await hass.async_block_till_done()
    assert hass.config_entries.async_entries(DOMAIN) == []
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, "yaml_import_failed_invalid_blind_code"
    )


async def test_remove_blind(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """Deleting a blind from the UI removes its entities."""
    entry = controller_entry(
        fake_controller, [blind_subentry(), blind_subentry("Blind Two", "021.230-05")]
    )
    await setup_entry(hass, entry)
    sub_id = next(
        s.subentry_id for s in entry.subentries.values() if s.title == "Blind Two"
    )
    assert hass.config_entries.async_remove_subentry(entry, sub_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert registry.async_get("cover.blind_two") is None
    assert hass.states.get("cover.blind_two") is None
    assert hass.states.get("cover.blind_one").state == "open"
