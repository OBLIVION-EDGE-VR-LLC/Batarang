#!/usr/bin/env python3
"""BLE/Bluetooth interactive shell using cmd2 and Bleak."""

import asyncio
import fnmatch
import struct
import sys
import threading
from typing import Optional

import cmd2
from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from bt_constants import manufacturer_name, service_name, characteristic_name


class BLEShell(cmd2.Cmd):
    """Interactive shell for Bluetooth Low Energy scanning, reading, and writing."""

    intro = "BLE Shell — type 'help' for commands, 'quit' to exit."
    prompt = "ble> "

    def __init__(self):
        super().__init__(allow_cli_args=False)
        self._client: Optional[BleakClient] = None
        self._device: Optional[BLEDevice] = None
        self._devices: list[BLEDevice] = []
        self._bt_name: Optional[str] = None
        # Persistent event loop in a background thread so all async
        # operations share the same loop (required by BleakClient).
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True
        )
        self._loop_thread.start()

    # ── helpers ──────────────────────────────────────────────────────

    def _run(self, coro):
        """Run an async coroutine on the persistent event loop."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def _require_connection(self) -> bool:
        if self._client is None or not self._client.is_connected:
            self.perror("Not connected. Use 'connect <address>' first.")
            return False
        return True

    def _display_name(self, address: str) -> str:
        """Return the custom BT name if set, otherwise the address."""
        return self._bt_name if self._bt_name else address

    @staticmethod
    def _serialize(value_str: str, dtype: str, encoding: str = "utf-8") -> bytes:
        """Convert a string value to bytes based on the chosen data type."""
        dt = dtype.lower()
        if dt == "byte":
            return struct.pack("B", int(value_str, 0))
        if dt == "int16":
            return struct.pack("<h", int(value_str, 0))
        if dt == "uint16":
            return struct.pack("<H", int(value_str, 0))
        if dt == "int32":
            return struct.pack("<i", int(value_str, 0))
        if dt == "uint32":
            return struct.pack("<I", int(value_str, 0))
        if dt == "float":
            return struct.pack("<f", float(value_str))
        if dt == "hex":
            clean = value_str.replace(" ", "").replace("0x", "")
            return bytes.fromhex(clean)
        if dt == "bytes" or dt == "bytearray":
            # Accept space-separated decimal or hex bytes: "0x41 65 0xFF"
            parts = value_str.split()
            return bytes(int(p, 0) for p in parts)
        if dt == "utf8":
            return value_str.encode("utf-8")
        if dt in ("str", "string"):
            return value_str.encode(encoding)
        raise ValueError(f"Unknown data type: {dtype}")

    # ── set_bt_name ──────────────────────────────────────────────────

    bt_name_parser = cmd2.Cmd2ArgumentParser(description="Set a custom display name for connections.")
    bt_name_parser.add_argument(
        "name", nargs="?", default=None,
        help="Custom name to display (omit to clear / show current)"
    )

    @cmd2.with_argparser(bt_name_parser)
    def do_set_bt_name(self, args):
        """Set or clear a custom Bluetooth display name for this session."""
        if args.name is None:
            if self._bt_name:
                self.poutput(f"Current BT name: {self._bt_name}")
            else:
                self.poutput("No custom BT name set.")
            return

        self._bt_name = args.name
        self.poutput(f"BT name set to: {self._bt_name}")
        # Update prompt if connected
        if self._client and self._client.is_connected:
            self.prompt = f"ble [{self._bt_name}]> "

    # ── scan ─────────────────────────────────────────────────────────

    @staticmethod
    def _match_filter(name: str, pattern: str) -> bool:
        """Match a device name against a wildcard pattern (case-insensitive).

        Examples:
            Baller*       → prefix match (starts with "Baller")
            *Caller       → suffix match (ends with "Caller")
            *Shot*        → contains match
            Baller*Caller → fnmatch glob
            ExactName     → exact match (case-insensitive)
        """
        return fnmatch.fnmatchcase(name.lower(), pattern.lower())

    scan_parser = cmd2.Cmd2ArgumentParser(description="Scan for BLE devices.")
    scan_parser.add_argument(
        "-t", "--timeout", type=float, default=5.0, help="Scan duration in seconds (default: 5)"
    )
    scan_parser.add_argument(
        "-s", "--search", type=str, default=None,
        help="Filter by name with wildcards (e.g. 'Baller*', '*Shot*', '*Caller')"
    )

    @cmd2.with_argparser(scan_parser)
    def do_scan(self, args):
        """Scan for nearby BLE devices, optionally filtering by name pattern."""
        self.poutput(f"Scanning for {args.timeout}s …")
        try:
            devices = self._run(BleakScanner.discover(timeout=args.timeout, return_adv=True))
        except Exception as e:
            self.perror(f"Scan failed: {e}")
            return

        self._devices = []
        rows = []
        for idx, (device, adv) in enumerate(devices.values()):
            name = device.name or "Unknown"
            if args.search and not self._match_filter(name, args.search):
                continue
            self._devices.append(device)
            # Decode manufacturer data
            mfr = ""
            if adv.manufacturer_data:
                mfr_ids = [manufacturer_name(mid) for mid in adv.manufacturer_data.keys()]
                mfr = ", ".join(mfr_ids)
            rows.append([str(len(self._devices) - 1), device.address, name, str(adv.rssi), mfr])

        if not rows:
            if args.search:
                self.poutput(f"No devices matching '{args.search}' found.")
            else:
                self.poutput("No devices found.")
            return

        if args.search:
            self.poutput(f"Filter: {args.search}")
        header = f"{'Idx':<5} {'Address':<20} {'Name':<30} {'RSSI':<6} {'Manufacturer'}"
        self.poutput(header)
        self.poutput("─" * len(header))
        for row in rows:
            self.poutput(f"{row[0]:<5} {row[1]:<20} {row[2]:<30} {row[3]:<6} {row[4]}")

    # ── connect / disconnect ─────────────────────────────────────────

    connect_parser = cmd2.Cmd2ArgumentParser(description="Connect to a BLE device.")
    connect_parser.add_argument(
        "target", help="Device MAC address or scan index number"
    )

    @cmd2.with_argparser(connect_parser)
    def do_connect(self, args):
        """Connect to a BLE device by address or scan index."""
        target = args.target
        device_name = None

        # Allow connecting by scan index
        try:
            idx = int(target)
            if 0 <= idx < len(self._devices):
                device = self._devices[idx]
                target = device.address
                device_name = device.name or "Unknown"
                self.poutput(f"Connecting to {device_name} [{target}] …")
            else:
                self.perror(f"Index {idx} out of range. Run 'scan' first.")
                return
        except ValueError:
            self.poutput(f"Connecting to {target} …")

        try:
            client = BleakClient(target)
            self._run(client.connect())
            self._client = client
            display = self._display_name(target)
            self.poutput(f"{display} Connected")
            self.prompt = f"ble [{display}]> "
        except Exception as e:
            self.perror(f"Connection failed: {e}")

    def do_disconnect(self, _args):
        """Disconnect from the current device."""
        if self._client is None:
            self.poutput("Not connected.")
            return
        display = self._display_name(self._client.address)
        try:
            self._run(self._client.disconnect())
        except Exception:
            pass
        self._client = None
        self.prompt = "ble> "
        self.poutput(f"{display} Disconnected.")

    # ── services ─────────────────────────────────────────────────────

    services_parser = cmd2.Cmd2ArgumentParser(description="List services and characteristics.")
    services_parser.add_argument(
        "target", nargs="?", default=None,
        help="MAC address or scan index to query without connecting (omit to use current connection)"
    )

    @cmd2.with_argparser(services_parser)
    def do_services(self, args):
        """List all services and characteristics. Optionally pass a MAC/index to query without connecting."""
        if args.target is not None:
            self._services_adhoc(args.target)
        else:
            self._services_connected()

    def _services_connected(self):
        """List services on the currently connected device."""
        if not self._require_connection():
            return
        self._print_services(self._client.services)

    def _services_adhoc(self, target: str):
        """Connect temporarily to list services, then disconnect."""
        # Resolve scan index
        try:
            idx = int(target)
            if 0 <= idx < len(self._devices):
                target = self._devices[idx].address
            else:
                self.perror(f"Index {idx} out of range. Run 'scan' first.")
                return
        except ValueError:
            pass

        self.poutput(f"Querying services on {target} …")

        async def _query():
            async with BleakClient(target) as client:
                return client.services

        try:
            services = self._run(_query())
            self._print_services(services)
        except Exception as e:
            self.perror(f"Failed to query services: {e}")

    def _print_services(self, services):
        """Pretty-print GATT services tree with resolved names from bt_constants."""
        for svc in services:
            svc_resolved = service_name(svc.uuid)
            self.poutput(f"\n[Service] {svc.uuid}  —  {svc_resolved}")
            for char in svc.characteristics:
                props = ", ".join(char.properties)
                char_resolved = characteristic_name(char.uuid)
                self.poutput(f"  ├── [Char] {char.uuid}  ({props})")
                self.poutput(f"  │         {char_resolved}")
                for desc in char.descriptors:
                    self.poutput(f"  │   └── [Desc] {desc.uuid}  {desc.description or ''}")

    # ── read ─────────────────────────────────────────────────────────

    read_parser = cmd2.Cmd2ArgumentParser(description="Read a characteristic value.")
    read_parser.add_argument("uuid", help="Characteristic UUID to read")

    @cmd2.with_argparser(read_parser)
    def do_read(self, args):
        """Read a characteristic value by UUID."""
        if not self._require_connection():
            return

        try:
            data = self._run(self._client.read_gatt_char(args.uuid))
        except Exception as e:
            self.perror(f"Read failed: {e}")
            return

        hex_str = data.hex(" ")
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
        self.poutput(f"Hex:   {hex_str}")
        self.poutput(f"ASCII: {ascii_str}")
        self.poutput(f"Raw:   {list(data)}")

    # ── write ────────────────────────────────────────────────────────

    WRITE_TYPES = [
        "byte", "bytes", "int16", "uint16", "int32", "uint32",
        "float", "hex", "bytearray", "utf8", "str", "string",
    ]
    STR_ENCODINGS = [
        "utf-8", "utf-16", "utf-32", "ascii", "latin-1",
        "iso-8859-1", "cp1252", "utf-16-le", "utf-16-be",
    ]

    write_parser = cmd2.Cmd2ArgumentParser(description="Write a value to a characteristic.")
    write_parser.add_argument("uuid", help="Characteristic UUID to write to")
    write_parser.add_argument("value", help="Value to write (format depends on --type)")
    write_parser.add_argument(
        "-t", "--type", choices=WRITE_TYPES, default="hex",
        help="Data type for serialization (default: hex)"
    )
    write_parser.add_argument(
        "-e", "--encoding", choices=STR_ENCODINGS, default="utf-8",
        help="Encoding for str/string types (default: utf-8)"
    )
    write_parser.add_argument(
        "-r", "--response", action="store_true", default=True,
        help="Request write response (default: True)"
    )
    write_parser.add_argument(
        "--no-response", dest="response", action="store_false",
        help="Write without response (write command, not write request)"
    )

    @cmd2.with_argparser(write_parser)
    def do_write(self, args):
        """Write a value to a characteristic."""
        if not self._require_connection():
            return

        try:
            data = self._serialize(args.value, args.type, args.encoding)
        except (ValueError, struct.error) as e:
            self.perror(f"Invalid value for type '{args.type}': {e}")
            return

        self.poutput(f"Writing {data.hex(' ')} ({len(data)} bytes) as {args.type} …")

        try:
            self._run(self._client.write_gatt_char(args.uuid, data, response=args.response))
            self.poutput("Write successful.")
        except Exception as e:
            self.perror(f"Write failed: {e}")

    # ── notify ───────────────────────────────────────────────────────

    notify_parser = cmd2.Cmd2ArgumentParser(description="Subscribe to notifications.")
    notify_parser.add_argument("uuid", help="Characteristic UUID to subscribe to")
    notify_parser.add_argument(
        "-d", "--duration", type=float, default=10.0,
        help="How long to listen in seconds (default: 10)"
    )

    @cmd2.with_argparser(notify_parser)
    def do_notify(self, args):
        """Subscribe to characteristic notifications for a duration."""
        if not self._require_connection():
            return

        async def _listen():
            def callback(_, data: bytearray):
                hex_str = data.hex(" ")
                self.async_alert(f"[Notify {args.uuid}] {hex_str}")

            await self._client.start_notify(args.uuid, callback)
            self.poutput(f"Listening for {args.duration}s … (Ctrl+C to stop early)")
            try:
                await asyncio.sleep(args.duration)
            except asyncio.CancelledError:
                pass
            await self._client.stop_notify(args.uuid)
            self.poutput("Stopped notifications.")

        try:
            self._run(_listen())
        except KeyboardInterrupt:
            self.poutput("\nStopped.")
        except Exception as e:
            self.perror(f"Notify failed: {e}")


def main():
    app = BLEShell()
    sys.exit(app.cmdloop())


if __name__ == "__main__":
    main()
