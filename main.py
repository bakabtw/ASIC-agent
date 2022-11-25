import os
import time
from pony import orm
import logging
import requests
from dragon_rest.dragons import DragonAPI
import routeros_api

# Time between checks
SLEEP_TIMER = 10
# Timeout for accessing ASIC
RESET_ASIC_TIMEOUT = 5
# Timeout for accessing Mikrotik router
MIKROTIK_ACCESS_TIMEOUT = 5
# URL for getting active power updates
URL = "http://127.0.0.1:8000/power.json"
# Router credential
ROUTER = {
    'ip': '192.168.88.1',
    'port': 8728,
    'username': 'admin',
    'password': 'aszpvo'
}

# Checking for DEBUG environment
if os.getenv('DEBUG'):
    logging.basicConfig(level=logging.INFO)

    if os.getenv('DEBUG') == 'verbose':
        orm.set_sql_debug(True)

# Creating DB
db = orm.Database()
db.bind(provider='sqlite', filename='asics.db', create_db=True)


# Defining a table for ASICs
class Hosts(db.Entity):
    id = orm.PrimaryKey(int, auto=True)
    ip = orm.Required(str)
    port = orm.Required(int)
    user = orm.Required(str)
    password = orm.Required(str)
    type = orm.Required(str)
    power = orm.Required(int)
    phase = orm.Required(str)
    power_group = orm.Required(int)
    online = orm.Required(str)


# Defining a table for power groups
class PowerGroups(db.Entity):
    id = orm.PrimaryKey(int, auto=True)
    total_power = orm.Required(int)
    online = orm.Required(str)


class AsicAgent:
    def __init__(self):
        self.sleep_timer = SLEEP_TIMER
        self.url = URL
        self.router = ROUTER
        self.reset_asic_timeout = RESET_ASIC_TIMEOUT
        self.mikrotik_access_timeout = MIKROTIK_ACCESS_TIMEOUT

        db.generate_mapping(create_tables=True)

        self.flush_access_rules()
        self.shutdown_all_asics()

    def run(self):
        """
            Algorithm:
            1. Download a json file with available power
            2. Parse json file, check values
            3. Get current power consumption
            4. Compare current power consumption with available power
            5. Disable/Enable ASICs if necessary
            6. GOTO 1
        """
        while True:
            # TODO: Add hysteresis for enabling ASICS
            available_power = self.get_available_power()
            active_power = self.get_active_power()

            self.update_power_groups()

            logging.info(f"Available power: {available_power}")
            logging.info(f"Active power: {active_power}")

            if available_power >= active_power:
                power_group = self.get_random_power_group(online='False')

                if power_group is not None and available_power - active_power > power_group.total_power:
                    for member in self.get_power_group_members(power_group.id):
                        self.enable_asic(
                            member.ip, member.port,
                            member.user, member.password
                        )
            else:
                power_group = self.get_random_power_group(online='True')

                for member in self.get_power_group_members(power_group.id):
                    self.disable_asic(
                        member.ip, member.port,
                        member.user, member.password
                    )

            self.show_status()
            time.sleep(self.sleep_timer)

    def get_available_power(self):
        data = {}

        try:
            r = requests.get(self.url)
            data = r.json()
        except Exception as e:
            logging.error(f"Download error {e}")
            data['success'] = False  # Fetching data wasn't successful

        if 'success' in data and data['success'] is True:
            return data['power']
        else:
            return 0

    @orm.db_session
    def get_active_power(self):
        hosts = Hosts.select(lambda p: p.online == 'True')
        active_power = 0

        for host in hosts:
            active_power += host.power

        return active_power

    @orm.db_session
    def get_random_power_group(self, online):
        power_group = PowerGroups.select(lambda p: p.online == online).random(1)

        if len(power_group) > 0:
            output = power_group[0]
        else:
            output = None

        return output

    @orm.db_session
    def get_power_group_members(self, power_group):
        members = Hosts.select(lambda p: p.power_group == power_group)
        output = []

        for member in members:
            output.append(member)

        return output

    @orm.db_session
    def update_power_groups(self):
        logging.info("Updating PowerGroups table")
        self.flush_power_groups()

        members = Hosts.select()

        for member in members:
            power_group = PowerGroups.get(lambda p: p.id == member.power_group)

            if power_group:
                power_group.total_power += member.power
            else:
                power_group = PowerGroups(
                    id=member.power_group,
                    total_power=member.power,
                    online=member.online
                )

    @orm.db_session
    def flush_power_groups(self):
        PowerGroups.select().delete(bulk=True)

    @orm.db_session
    def shutdown_all_asics(self):
        logging.info("Shutting down all ASICs")
        hosts = Hosts.select()

        for host in hosts:
            host.online = 'False'
            self.disable_internet_access(host.ip)

    @orm.db_session
    def update_asic_status(self, ip, online):
        host = Hosts.get(lambda p: p.ip == ip)
        host.online = online

    def disable_asic(self, ip, port, user, password):
        logging.info(f"Shutting down ASIC: {ip}:{port}")

        self.update_asic_status(ip, online='False')
        self.restart_asic(ip, port, user, password)
        self.disable_internet_access(ip)

    def enable_asic(self, ip, port, user, password):
        logging.info(f"Starting ASIC: {ip}:{port}")

        self.update_asic_status(ip, online='True')
        self.enable_internet_access(ip)

    def disable_internet_access(self, ip):
        logging.info(f"Disabling internet access for: {ip}")

        try:
            api = self.get_routeros_api()

            list_address = api.get_resource('/ip/firewall/address-list')
            list_address.add(address=ip, list="BL")
        except Exception as e:
            logging.error(f"Error while disabling internet access for {ip}: {e}")

    def enable_internet_access(self, ip):
        logging.info(f"Enabling internet access for: {ip}")

        try:
            api = self.get_routeros_api()

            list_address = api.get_resource('/ip/firewall/address-list')
            rule_id = list_address.detailed_get(address=ip)[0]['id']
            list_address.remove(id=rule_id)
        except Exception as e:
            logging.error(f"Error while enabling internet access for {ip}: {e}")

    def flush_access_rules(self):
        logging.info("Flushing internet access rules")

        try:
            api = self.get_routeros_api()

            list_address = api.get_resource('/ip/firewall/address-list')
            rules = list_address.detailed_get()

            for rule in rules:
                list_address.remove(id=rule['id'])
        except Exception as e:
            logging.error(f"Error while flushing internet access rules: {e}")

    def get_routeros_api(self):
        # Establishing connection with Mikrotik API
        mk_connection = routeros_api.RouterOsApiPool(
            self.router['ip'],
            port=self.router['port'],
            username=self.router['username'],
            password=self.router['password'],
            plaintext_login=True
        )
        mk_connection.set_timeout(self.mikrotik_access_timeout)
        api = mk_connection.get_api()

        return api

    def restart_asic(self, ip, port, user, password):
        logging.info(f"Restarting ASIC: {ip}:{port}")

        try:
            api = DragonAPI(f"{ip}:{port}",
                            username=user,
                            password=password,
                            timeout=self.reset_asic_timeout)

            api.restartCgMiner()
        except Exception as e:
            logging.error(f"Error occurred during restarting an ASIC ({ip}): {e}")

    @orm.db_session
    def show_status(self):
        hosts = Hosts.select()

        logging.info("id ip power power_group online")

        for host in hosts:
            logging.info([
                host.id,
                host.ip,
                host.power,
                host.power_group,
                host.online]
            )


if __name__ == '__main__':
    AsicAgent().run()
