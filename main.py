import os
import time
from pony import orm
import logging
import requests
from dragon_rest.dragons import DragonAPI

# Time between checks
SLEEP_TIMER = 15
URL = "https://power.knst.me/power.json"

# Checking for DEBUG environment
if os.getenv('DEBUG'):
    orm.set_sql_debug(True)
    logging.basicConfig(level=logging.INFO)

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
        # TODO: Update power groups based on Hosts data on start up
        self.sleep_timer = SLEEP_TIMER
        self.url = URL

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
        db.generate_mapping(create_tables=True)

        while True:
            available_power = self.get_available_power()
            active_power = self.get_active_power()

            self.update_power_groups()

            logging.info(f"Available power: {available_power}")
            logging.info(f"Active power: {active_power}")

            if available_power > active_power:
                power_group = self.get_random_power_group(online='False')

                if available_power > power_group.total_power:
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

                continue

            time.sleep(self.sleep_timer)

    def get_available_power(self):
        # TODO: Add error handling
        data = {}

        try:
            r = requests.get(self.url)
            data = r.json()
        except Exception as e:
            logging.error(f"Download error {e}")
            data['success'] = False  # Fetching data wasn't successful

        # TODO: Check if data['success'] is not present
        if data['success'] is True:
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

        # TODO: Add a check for an empty list
        output = power_group[0]

        return output

    @orm.db_session
    def get_power_group_members(self, power_group):
        members = Hosts.select(lambda p: p.power_group == power_group)
        output = []

        for member in members:
            output.append(member)

        return output

    def update_power_groups(self):
        pass

    def disable_asic(self, ip, port, user, password):
        # TODO: Update DB once triggered
        logging.info(f"Shutting down ASIC: {ip}:{port}")
        self.disable_internet_access(ip)
        self.restart_asic(ip, port, user, password)

    def enable_asic(self, ip, port, user, password):
        # TODO: Update DB once triggered
        logging.info(f"Starting ASIC: {ip}:{port}")
        self.enable_internet_access(ip)

    def disable_internet_access(self, ip):
        logging.info(f"Disabling internet access for: {ip}")
        pass

    def enable_internet_access(self, ip):
        logging.info(f"Enabling internet access for: {ip}")
        pass

    def restart_asic(self, ip, port, user, password):
        logging.info(f"Restarting ASIC: {ip}:{port}")
        api = DragonAPI(f"{ip}:{port}",
                        username=user,
                        password=password)

        api.restartCgMiner()


if __name__ == '__main__':
    AsicAgent().run()
