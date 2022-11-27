# ASIC power management agent

A simple app for managing your ASIC farm based on available grid power

# NB!
This is the alfa version!

# Prerequisites
It's important to add a firewall rule in RouterOS for blocking internet access based on a black list

```commandline
/ip firewall filter add chain=forward action=reject reject-with=icmp-network-unreachable src-address-list="BL"
```

# Usage
```python
    python3 main.py
```

# Requirements
- requests
- pony
- dragon_rest
- routeros_api
- influxdb_client