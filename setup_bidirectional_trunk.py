#!/usr/bin/env python3
"""Production setup: one DID for inbound + outbound via Vobiz + LiveKit.

Updates:
  - LiveKit inbound trunk (receive PSTN → agent dispatch)
  - LiveKit outbound trunk (agent → PSTN, same caller ID)
  - Dispatch rule → LIVEKIT_AGENT_NAME
  - Vobiz inbound_destination → LiveKit SIP URI (when API creds are set)
  - .env: SIP_TRUNK_ID, OUTBOUND_TRUNK_ID, SIP_DISPATCH_RULE_ID, INBOUND_PHONE_NUMBERS,
          LIVEKIT_SIP_URI, VOBIZ_TRUNK_ID
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from livekit import api

load_dotenv(".env", override=True)

ENV_PATH = ".env"
DEFAULT_SIP_PORT = 5060


def _normalize_livekit_api_url(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        return ""
    if value.startswith("wss://"):
        return "https://" + value[len("wss://") :]
    if value.startswith("ws://"):
        return "http://" + value[len("ws://") :]
    return value


def _livekit_api_url() -> str:
    for key in ("LIVEKIT_API_URL", "LIVEKIT_URL"):
        normalized = _normalize_livekit_api_url(os.getenv(key, ""))
        if normalized:
            return normalized
    return ""


def _persist_env_key(key: str, value: str) -> None:
    if not os.path.exists(ENV_PATH):
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    prefix = f"{key}="
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}={value}\n"
            updated = True
            break
    if not updated:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _livekit_host() -> str:
    for key in ("LIVEKIT_SIP_HOST", "LIVEKIT_API_URL", "LIVEKIT_URL"):
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        if "://" in raw:
            host = urlparse(raw).hostname
            if host:
                return host
        return raw.split(":")[0]
    return ""


def _resolve_livekit_sip_uri() -> str:
    explicit = (os.getenv("LIVEKIT_SIP_URI") or "").strip()
    if explicit:
        return explicit.removeprefix("sip:")

    host = _livekit_host()
    if not host:
        return ""

    port = (os.getenv("LIVEKIT_SIP_PORT") or str(DEFAULT_SIP_PORT)).strip()
    if host.endswith(".sip.livekit.cloud"):
        return host
    return f"{host}:{port}" if port else host


def _vobiz_inbound_trunk_id() -> str:
    explicit = (os.getenv("VOBIZ_INBOUND_TRUNK_ID") or "").strip()
    if explicit:
        return explicit
    fallback = (os.getenv("VOBIZ_TRUNK_ID") or "").strip()
    if fallback:
        print(
            "Warning: VOBIZ_INBOUND_TRUNK_ID not set; falling back to VOBIZ_TRUNK_ID. "
            "Inbound usually needs a separate inbound trunk UUID."
        )
    return fallback


def _vobiz_headers() -> dict[str, str]:
    return {
        "X-Auth-ID": (os.getenv("VOBIZ_AUTH_ID") or "").strip(),
        "X-Auth-Token": (os.getenv("VOBIZ_AUTH_TOKEN") or "").strip(),
        "Content-Type": "application/json",
    }


def _livekit_sip_invite_uri(host_port: str) -> str:
    cleaned = host_port.strip().removeprefix("sip:")
    return cleaned if cleaned.startswith("sip:") else f"sip:{cleaned}"


def _verify_sip_reachable(sip_uri: str) -> bool:
    host = sip_uri.removeprefix("sip:")
    port = DEFAULT_SIP_PORT
    if ":" in host:
        host, _, port_text = host.rpartition(":")
        try:
            port = int(port_text)
        except ValueError:
            host = sip_uri.removeprefix("sip:")
            port = DEFAULT_SIP_PORT
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        branch = "z9hG4bK-setup-check"
        options = (
            f"OPTIONS sip:health@{host}:{port} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP 127.0.0.1:5060;branch={branch}\r\n"
            f"Max-Forwards: 70\r\n"
            f"To: <sip:health@{host}:{port}>\r\n"
            f"From: <sip:health@127.0.0.1>;tag=setup\r\n"
            f"Call-ID: setup-check@127.0.0.1\r\n"
            f"CSeq: 1 OPTIONS\r\n"
            f"Contact: <sip:health@127.0.0.1:5060>\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        sock.sendto(options.encode(), (host, port))
        data, _ = sock.recvfrom(4096)
        sock.close()
        return data.decode("utf-8", errors="replace").startswith("SIP/2.0")
    except OSError:
        return False


def _inbound_number_variants(number: str) -> list[str]:
    normalized = number.strip()
    local = normalized.replace("+91", "")
    return sorted(
        {
            normalized,
            normalized.lstrip("+"),
            local,
            f"91{local}" if local else "",
            f"0{local}" if local else "",
        }
        - {""}
    )


def _verify_vobiz_did_on_trunk(auth_id: str, headers: dict[str, str], trunk_id: str, number: str) -> tuple[bool, str]:
    try:
        response = requests.get(
            f"https://api.vobiz.ai/api/v1/Account/{auth_id}/numbers?limit=100",
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        items = response.json().get("items") or []
        for item in items:
            if item.get("e164") == number:
                linked = (item.get("trunk_group_id") or "").strip()
                if linked == trunk_id:
                    return True, f"DID {number} is linked to inbound trunk"
                return False, f"DID {number} is linked to trunk {linked}, not {trunk_id}"
        return False, f"DID {number} not found on Vobiz account"
    except requests.RequestException as exc:
        return False, str(exc)


def _find_or_create_vobiz_origination_uri(
    auth_id: str,
    headers: dict[str, str],
    trunk_id: str,
    target_uri: str,
) -> tuple[str | None, str]:
    list_url = f"https://api.vobiz.ai/api/v1/Account/{auth_id}/trunks/{trunk_id}/origination-uris"
    try:
        listed = requests.get(list_url, headers=headers, timeout=20)
        listed.raise_for_status()
        for item in listed.json().get("objects") or []:
            if (item.get("uri") or "").strip() == target_uri:
                return item.get("id"), "existing origination URI"

        payload = {"name": "LiveKit SIP", "uri": target_uri, "priority": 1}
        created = requests.post(list_url, headers=headers, json=payload, timeout=20)
        if created.status_code < 400:
            body = created.json()
            uri_id = (body.get("id") or "").strip()
            if uri_id:
                return uri_id, "created origination URI"
            return None, "origination URI created but response had no id"

        if created.status_code >= 500 and "unique_trunk" in created.text:
            relisted = requests.get(list_url, headers=headers, timeout=20)
            relisted.raise_for_status()
            for item in relisted.json().get("objects") or []:
                uri_value = (item.get("uri") or "").strip()
                if uri_value == target_uri:
                    return item.get("id"), "reused origination URI after unique-trunk conflict"
                if not uri_value:
                    return None, (
                        "Vobiz trunk already has an origination URI with an empty destination. "
                        "Delete it in Vobiz Console, then re-run this script."
                    )
            return None, created.text[:300]

        return None, created.text[:300]
    except requests.RequestException as exc:
        return None, str(exc)


def _configure_vobiz_inbound(sip_uri: str, trunk_id: str, number: str) -> tuple[bool, str]:
    auth_id = (os.getenv("VOBIZ_AUTH_ID") or "").strip()
    headers = _vobiz_headers()
    if not (headers["X-Auth-ID"] and headers["X-Auth-Token"] and trunk_id):
        return False, "missing VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN / VOBIZ_INBOUND_TRUNK_ID"

    trunk_url = f"https://api.vobiz.ai/api/v1/Account/{auth_id}/trunks/{trunk_id}"
    target_uri = _livekit_sip_invite_uri(sip_uri)
    try:
        current = requests.get(trunk_url, headers=headers, timeout=20)
        if current.status_code == 404:
            return False, f"trunk {trunk_id} not found for auth_id {auth_id}"
        current.raise_for_status()
        trunk = current.json()
        if trunk.get("trunk_direction") not in ("inbound", "both"):
            return False, (
                f"trunk {trunk_id} is {trunk.get('trunk_direction')}; "
                "use VOBIZ_INBOUND_TRUNK_ID for the inbound trunk"
            )

        did_ok, did_msg = _verify_vobiz_did_on_trunk(auth_id, headers, trunk_id, number)
        if not did_ok:
            return False, did_msg

        uri_id, uri_note = _find_or_create_vobiz_origination_uri(
            auth_id, headers, trunk_id, target_uri
        )
        if not uri_id:
            return False, uri_note or "could not create Vobiz origination URI"

        existing_dest = (trunk.get("inbound_destination") or "").strip()
        existing_primary = (trunk.get("primary_uri_uuid") or "").strip()
        if existing_dest == uri_id and existing_primary == uri_id:
            return True, f"{uri_note}; primary URI already points to {target_uri}"

        response = requests.put(
            trunk_url,
            headers=headers,
            json={
                "name": trunk.get("name") or "speaksy-inbound",
                "max_concurrent_calls": trunk.get("concurrent_calls_limit") or 10,
                "enabled": trunk.get("trunk_status") == "active",
                "inbound_destination": uri_id,
                "primary_uri_uuid": uri_id,
            },
            timeout=20,
        )
        if response.status_code < 400:
            return True, f"{uri_note}; primary URI → {target_uri}"
        return False, response.text[:300]
    except requests.RequestException as exc:
        return False, str(exc)


async def main() -> int:
    domain = (os.getenv("VOBIZ_SIP_DOMAIN") or "").strip()
    username = (os.getenv("VOBIZ_USERNAME") or "").strip()
    password = (os.getenv("VOBIZ_PASSWORD") or "").strip()
    number = (os.getenv("VOBIZ_OUTBOUND_NUMBER") or "").strip()
    agent_name = (os.getenv("LIVEKIT_AGENT_NAME") or "inbound-voice-demo").strip()
    inbound_id = (os.getenv("SIP_TRUNK_ID") or "").strip()
    outbound_id = (os.getenv("OUTBOUND_TRUNK_ID") or "").strip()
    preserve_rule = (os.getenv("SIP_DISPATCH_RULE_ID") or "").strip()
    vobiz_inbound_trunk_id = _vobiz_inbound_trunk_id()
    sip_uri = _resolve_livekit_sip_uri()

    missing = [
        name
        for name, val in [
            ("VOBIZ_SIP_DOMAIN", domain),
            ("VOBIZ_USERNAME", username),
            ("VOBIZ_PASSWORD", password),
            ("VOBIZ_OUTBOUND_NUMBER", number),
        ]
        if not val
    ]
    if missing:
        print(f"Error: missing .env values: {', '.join(missing)}")
        return 1
    if not sip_uri:
        print("Error: could not resolve LIVEKIT_SIP_URI. Set LIVEKIT_SIP_URI or LIVEKIT_URL.")
        return 1

    try:
        from livekit_api_client import create_livekit_api

        lk = create_livekit_api()
    except Exception as exc:
        print(f"Error: could not create LiveKit API client: {exc}")
        return 1
    sip = lk.sip

    async def _update_or_create_outbound(trunk_id: str) -> str:
        if trunk_id:
            print(f"Updating outbound trunk {trunk_id} …")
            try:
                out = await sip.update_outbound_trunk_fields(
                    trunk_id,
                    name="speaksy-outbound",
                    address=domain,
                    auth_username=username,
                    auth_password=password,
                    numbers=[number],
                )
                print(f"  ✓ outbound {out.sip_trunk_id} → {domain} nums={list(out.numbers)}")
                return out.sip_trunk_id
            except api.TwirpError as exc:
                if exc.code != "not_found":
                    raise
                print(f"  ⚠ outbound trunk {trunk_id} missing — creating new one")
        print("Creating outbound trunk speaksy-outbound …")
        out = await sip.create_outbound_trunk(
            api.CreateSIPOutboundTrunkRequest(
                trunk=api.SIPOutboundTrunkInfo(
                    name="speaksy-outbound",
                    address=domain,
                    auth_username=username,
                    auth_password=password,
                    numbers=[number],
                )
            )
        )
        print(f"  ✓ created outbound {out.sip_trunk_id}")
        return out.sip_trunk_id

    async def _update_or_create_inbound(trunk_id: str) -> str:
        if trunk_id:
            print(f"Updating inbound trunk {trunk_id} …")
            try:
                inn = await sip.update_inbound_trunk_fields(
                    trunk_id,
                    name="speaksy-inbound",
                    numbers=_inbound_number_variants(number),
                    allowed_addresses=["0.0.0.0/0"],
                    auth_username="",
                    auth_password="",
                )
                print(f"  ✓ inbound {inn.sip_trunk_id} nums={list(inn.numbers)}")
                return inn.sip_trunk_id
            except api.TwirpError as exc:
                if exc.code != "not_found":
                    raise
                print(f"  ⚠ inbound trunk {trunk_id} missing — creating new one")
        print("Creating inbound trunk speaksy-inbound …")
        inn = await sip.create_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name="speaksy-inbound",
                    numbers=_inbound_number_variants(number),
                    allowed_addresses=["0.0.0.0/0"],
                    auth_username="",
                    auth_password="",
                    krisp_enabled=True,
                )
            )
        )
        print(f"  ✓ created inbound {inn.sip_trunk_id}")
        return inn.sip_trunk_id

    try:
        outbound_id = await _update_or_create_outbound(outbound_id)
        inbound_id = await _update_or_create_inbound(inbound_id)

        rules = await sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())
        rule_id = None
        for r in rules.items:
            if preserve_rule and r.sip_dispatch_rule_id == preserve_rule:
                agents = [a.agent_name for a in (r.room_config.agents or [])]
                if inbound_id in list(r.trunk_ids) and agent_name in agents:
                    rule_id = preserve_rule
                    print(f"Keeping dispatch rule {preserve_rule} (already correct)")
                    continue
            req = api.DeleteSIPDispatchRuleRequest()
            req.sip_dispatch_rule_id = r.sip_dispatch_rule_id
            await sip.delete_dispatch_rule(req)
            print(f"Deleted dispatch rule {r.sip_dispatch_rule_id}")

        if not rule_id:
            agent_dispatch = api.RoomAgentDispatch()
            agent_dispatch.agent_name = agent_name
            room_config = api.RoomConfiguration()
            room_config.empty_timeout = 30
            room_config.agents.append(agent_dispatch)

            individual = api.SIPDispatchRuleIndividual()
            individual.room_prefix = "call-"

            dispatch_rule = api.SIPDispatchRule()
            dispatch_rule.dispatch_rule_individual.CopyFrom(individual)

            req = api.CreateSIPDispatchRuleRequest()
            req.rule.CopyFrom(dispatch_rule)
            req.trunk_ids.append(inbound_id)
            req.room_config.CopyFrom(room_config)
            req.name = "speaksy-inbound-to-agent"

            result = await sip.create_dispatch_rule(req)
            rule_id = result.sip_dispatch_rule_id
            print(f"  ✓ dispatch rule {rule_id} → agent '{agent_name}'")

        _persist_env_key("SIP_TRUNK_ID", inbound_id)
        _persist_env_key("OUTBOUND_TRUNK_ID", outbound_id)
        _persist_env_key("SIP_DISPATCH_RULE_ID", rule_id)
        _persist_env_key("INBOUND_PHONE_NUMBERS", number)
        _persist_env_key("LIVEKIT_SIP_URI", sip_uri)
        if vobiz_inbound_trunk_id:
            _persist_env_key("VOBIZ_INBOUND_TRUNK_ID", vobiz_inbound_trunk_id)

        sip_ok = _verify_sip_reachable(sip_uri)
        print("\n✅ LiveKit bidirectional trunk setup complete")
        print(f"   INBOUND  SIP_TRUNK_ID={inbound_id}")
        print(f"   OUTBOUND OUTBOUND_TRUNK_ID={outbound_id}")
        print(f"   DISPATCH SIP_DISPATCH_RULE_ID={rule_id}")
        print(f"   DID      {number}")
        print(f"   SIP URI  {sip_uri} ({'reachable' if sip_ok else 'NOT reachable — check firewall'})")

        vobiz_ok, vobiz_msg = _configure_vobiz_inbound(sip_uri, vobiz_inbound_trunk_id, number)
        if vobiz_ok:
            print(f"\n✅ Vobiz inbound routing configured ({vobiz_msg})")
        else:
            print("\n── Vobiz inbound trunk (required for INBOUND) ──")
            print(f"  primary URI must route to {_livekit_sip_invite_uri(sip_uri)}")
            print(f"  Trunk UUID: {vobiz_inbound_trunk_id or '(set VOBIZ_INBOUND_TRUNK_ID)'}")
            print("  Console: https://console.vobiz.ai → SIP Trunk → inbound trunk → Primary URI")
            if "missing" in vobiz_msg:
                print("  Or add to .env and re-run:")
                print("    VOBIZ_AUTH_ID=MA_xxxx")
                print("    VOBIZ_AUTH_TOKEN=your_token")
                print("    VOBIZ_INBOUND_TRUNK_ID=7a5d653a-956d-4e25-88bb-174b1cecaf47")
            else:
                print(f"  API note: {vobiz_msg}")

        print("\nRestart agent after .env changes:")
        print("  python3 agent.py dev")
        return 0
    except Exception as e:
        print(f"\n❌ Setup failed: {e}")
        return 1
    finally:
        await lk.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))