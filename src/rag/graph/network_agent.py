"""Scratch-build network agent (Run feature).

When a run builds its ephemeral network "from scratch" (rather than cloning an
example), this bounded tool-calling agent configures the fresh network so the test
can exercise it realistically — creating an SSID for wireless, VLAN/firewall rules
for a security appliance, a quality profile for a camera, etc. It follows the same
provider-agnostic model plumbing as AutoTestLLM (build_chat_model), binding the
Meraki config-write calls as tools the model may call.

Best-effort by design: a model that can't reach its backend surfaces as
``ScratchConfigError`` (the run then errors); a single failed tool call is reported
back to the model and the loop continues.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from rag.provider import ProviderConfig, build_chat_model, is_connection_error
from web.services import meraki

logger = logging.getLogger("rag.network_agent")

MAX_ROUNDS = 8   # cap tool-calling turns so a confused model can't loop forever


class ScratchConfigError(Exception):
    """The scratch-build agent could not configure the network (hard failure)."""


_SYSTEM = (
    "You configure a brand-new, empty Cisco Meraki network so a specific API test can "
    "run against it realistically. You are given the network id, the hardware that was "
    "just claimed into it, and what the test needs. Use the provided tools to apply a "
    "minimal but functional configuration for each hardware type present: for wireless, "
    "enable and name an SSID; for a security appliance, add a VLAN and a sane firewall "
    "rule; for a camera, create a quality/retention profile. Call tools only for hardware "
    "that is actually present. When the network is adequately configured, reply with the "
    "single word DONE and no tool calls."
)


def _build_tools(network_id, key, on_log):
    def _log(msg, level="info"):
        if on_log:
            on_log({"stage": "configure", "level": level, "message": msg})

    @tool
    def configure_ssid(name: str, enabled: bool = True, number: int = 0) -> str:
        """Enable and name a wireless SSID on the network (number 0-14)."""
        meraki.update_ssid(network_id, number, {"name": name, "enabled": enabled}, key)
        _log(f"SSID {number} set to {name!r} (enabled={enabled})")
        return f"ssid {number} configured"

    @tool
    def configure_appliance_vlan(vlan_id: str, name: str, subnet: str, appliance_ip: str) -> str:
        """Create a security-appliance VLAN (e.g. subnet '192.168.10.0/24', appliance_ip '192.168.10.1')."""
        meraki.create_appliance_vlan(
            network_id, {"id": vlan_id, "name": name, "subnet": subnet, "applianceIp": appliance_ip}, key)
        _log(f"VLAN {vlan_id} ({name}) created on {subnet}")
        return f"vlan {vlan_id} created"

    @tool
    def set_firewall_rules(rules: list) -> str:
        """Replace the L3 firewall rules (list of {comment, policy, protocol, srcCidr, destCidr})."""
        meraki.update_appliance_firewall_rules(network_id, rules, key)
        _log(f"applied {len(rules)} firewall rule(s)")
        return f"{len(rules)} firewall rule(s) applied"

    @tool
    def configure_camera_profile(name: str) -> str:
        """Create a camera quality-and-retention profile by name."""
        meraki.update_camera_quality_retention(network_id, {"name": name}, key)
        _log(f"camera profile {name!r} created")
        return f"camera profile {name} created"

    return [configure_ssid, configure_appliance_vlan, set_firewall_rules, configure_camera_profile]


def configure_scratch_network(network_id, hardware_reqs, claimed_devices, key,
                              provider_overrides=None, on_log=None) -> None:
    """Drive the model to configure ``network_id`` for the claimed hardware. Raises
    ScratchConfigError only on a hard failure (can't reach the model backend)."""
    try:
        config = ProviderConfig(**(provider_overrides or {}))
        chat = build_chat_model(config)
    except Exception as exc:
        raise ScratchConfigError(f"Could not start the configuration agent: {exc}") from exc

    tools = _build_tools(network_id, key, on_log)
    by_name = {t.name: t for t in tools}
    model = chat.bind_tools(tools)

    hw_summary = ", ".join(f"{r.get('count', 1)}× {r.get('type')}" for r in hardware_reqs or []) or "none"
    dev_summary = ", ".join(f"{d.get('hardwareType')} {d.get('serial')}" for d in claimed_devices or []) or "none"
    messages = [
        SystemMessage(_SYSTEM),
        HumanMessage(
            f"Network id: {network_id}\nHardware required: {hw_summary}\n"
            f"Claimed devices: {dev_summary}\nConfigure the network now."
        ),
    ]

    for _ in range(MAX_ROUNDS):
        try:
            ai = model.invoke(messages)
        except Exception as exc:
            if is_connection_error(exc):
                raise ScratchConfigError(f"Model backend unreachable during configuration: {exc}") from exc
            logger.warning("scratch agent invoke failed: %s", exc)
            return  # best-effort: stop configuring rather than fail the whole run
        messages.append(ai)
        calls = getattr(ai, "tool_calls", None) or []
        if not calls:
            return  # model signalled completion (DONE) or has nothing more to do
        for call in calls:
            fn = by_name.get(call.get("name"))
            try:
                result = fn.invoke(call.get("args", {})) if fn else f"unknown tool {call.get('name')}"
            except meraki.MerakiError as exc:
                result = f"error: {exc}"   # report back so the model can adapt
            except Exception as exc:  # noqa: BLE001
                logger.warning("tool %s failed: %s", call.get("name"), exc)
                result = f"error: {exc}"
            messages.append(ToolMessage(content=str(result), tool_call_id=call.get("id", "")))
    logger.info("scratch agent hit MAX_ROUNDS for network %s", network_id)
