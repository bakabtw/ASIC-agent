import os
import time
from pony import orm
import logging
import requests
from dragon_rest.dragons import DragonAPI
import routeros_api
from influxdb_client import InfluxDBClient, Point


class AsicAgent:
    def __init__(self):
        # Setting up variables
        self.sleep_timer = int(os.getenv('SLEEP_TIMER')) if os.getenv('SLEEP_TIMER') else SLEEP_TIMER
        self.url = os.getenv('URL') or URL
        self.router = {
            'ip': os.getenv('ROUTER_IP'),
            'port': int(os.getenv('ROUTER_PORT')),
            'username': os.getenv('ROUTER_USERNAME'),
            'password': os.getenv('ROUTER_PASSWORD')
        } if os.getenv('ROUTER_IP') else ROUTER
        self.reset_asic_timeout = int(os.getenv('RESET_ASIC_TIMEOUT')) if os.getenv('RESET_ASIC_TIMEOUT')\
            else RESET_ASIC_TIMEOUT
        self.mikrotik_access_timeout = int(os.getenv('MIKROTIK_ACCESS_TIMEOUT')) if os.getenv('MIKROTIK_ACCESS_TIMEOUT') \
            else MIKROTIK_ACCESS_TIMEOUT
        self.influxdb = {
            'host': os.getenv('INFLUX_HOST'),
            'port': int(os.getenv('INFLUX_PORT')),
            'token': os.getenv('INFLUX_TOKEN'),
            'org': os.getenv('INFLUX_ORG'),
            'bucket': os.getenv('INFLUX_BUCKET')
        } if os.getenv('INFLUX_HOST') else INFLUXDB

        # Generating DB mapping
        db.generate_mapping(create_tables=True)

        # Flushing firewall rules and shutting down all ASIC
        self.flush_access_rules()
        self.shutdown_all_asics()

    def run(self):
        """
            Algorithm:
            1. Download a json file with available power
            2. Parse the json file
            3. Get current power consumption
            4. Compare current power consumption with available power
            5. Disable/Enable ASICs if necessary
            5.1 Enabling an ASIC consists of enabling internet access for the ASIC
            5.2 Disabling an ASIC consists of disabling internet access for the ASIC and restarting CGMiner
            6. GOTO 1
        """
        while True:
            # TODO: Add hysteresis for enabling ASICS
            # Getting available and active power
            available_power = self.get_available_power()
            active_power = self.get_active_power()

            # Updating PowerGroup table
            self.update_power_groups()

            logging.info(f"Available power: {available_power}")
            logging.info(f"Active power: {active_power}")

            # Checking available power against active power
            if available_power >= active_power:
                # Fetching a random power group with the attribute online='False'
                power_group = self.get_random_power_group(online='False')

                # Checking whether there's a power group available, and we have enough available power to turn it on
                if power_group is not None and available_power - active_power > power_group.total_power:
                    # Iterating through members of a power group to turn them on
                    for member in self.get_power_group_members(power_group.id):
                        self.enable_asic(
                            member.ip, member.port,
                            member.user, member.password
                        )
            else:
                # Fetching a power group with the attribute online='True'
                power_group = self.get_random_power_group(online='True')

                for member in self.get_power_group_members(power_group.id):
                    # Iterating through members of a power group to turn them off
                    self.disable_asic(
                        member.ip, member.port,
                        member.user, member.password
                    )

            # Sending stats to InfluxDB
            self.write_logs(available_power, active_power)
            # Sleeping before the next iteration
            time.sleep(self.sleep_timer)

    def get_available_power(self):
        """
        Gets and returns available power

        Returns
        -------
        available_power
            A number representing available power (in Watts)
        """
        data = {}

        try:
            # Fetching and parsing a json file with available power
            r = requests.get(self.url)
            data = r.json()
        except Exception as e:
            logging.error(f"Download error {e}")
            data['success'] = False  # Fetching data wasn't successful

        if 'success' in data and data['success'] is True and data['power'] >= 0:
            # If data was received successfully
            available_power = data['power']
        else:
            # Default value
            available_power = 0

        return available_power

    @orm.db_session
    def get_active_power(self):
        """
        Calculates and returns active power (in Watts)

        Returns
        -------
        active_power
            A number representing active power (in Watts)
        """
        # Fetching all online ASICs
        hosts = Hosts.select(lambda p: p.online == 'True')
        active_power = 0

        # Iterating through the list to calculate total active power
        for host in hosts:
            active_power += host.power

        return active_power

    @orm.db_session
    def get_random_power_group(self, online):
        """
        Returns a random power group

        Parameters
        ----------
        online : str ('True' or 'False')
            The status of a power group

        Returns
        -------
        output
            A power group data
        """
        # Fetching a random power group
        power_group = PowerGroups.select(lambda p: p.online == online).random(1)

        # Checking if there's a power group meeting the criteria
        if len(power_group) > 0:
            output = power_group[0]
        else:
            output = None

        return output

    @orm.db_session
    def get_power_group_members(self, power_group):
        """
        Returns a list of members of the power group

        Parameters
        ----------
        power_group
            A power group id

        Returns
        -------
        output
            A list of members of the power group
        """
        # Fetching a list of members in the power group
        members = Hosts.select(lambda p: p.power_group == power_group)
        output = []

        # Adding members to a list
        for member in members:
            output.append(member)

        return output

    @orm.db_session
    def update_power_groups(self):
        """
        Updates all power groups based on the info from Hosts tables
        """
        logging.info("Updating PowerGroups table")

        # Deleting all entries in the PowerGroups table
        self.flush_power_groups()

        # Fetching all ASICs
        members = Hosts.select()

        for member in members:
            # Fetching a power group
            power_group = PowerGroups.get(lambda p: p.id == member.power_group)

            if power_group:
                # If the power group exists, then update total_power
                power_group.total_power += member.power
            else:
                # If it doesn't exist, then create a new entry
                power_group = PowerGroups(
                    id=member.power_group,
                    total_power=member.power,
                    online=member.online
                )

    @orm.db_session
    def flush_power_groups(self):
        """
        Deletes all entries in the PowerGroup table
        """
        PowerGroups.select().delete(bulk=True)

    @orm.db_session
    def shutdown_all_asics(self):
        """
        Shutdowns all ASICs
        """
        logging.info("Shutting down all ASICs")

        # Fetching all ASICs
        hosts = Hosts.select()

        for host in hosts:
            # Changing the ASIC's online status
            host.online = 'False'
            # Disabling internet access for the ASIC
            self.disable_internet_access(host.ip)

    @orm.db_session
    def update_asic_status(self, ip, online):
        """
        Updates ASIC's status

        Parameters
        ----------
        ip
            ASIC's IP
        online
            ASIC's status to be set
        """
        # Fetching the entry
        host = Hosts.get(lambda p: p.ip == ip)
        # Changing status
        host.online = online

    def disable_asic(self, ip, port, user, password):
        """
        Disables an ASIC
        It's achieved via disabling internet access and restarting CGMiner

        Parameters
        ----------
        ip
            ASIC's IP
        port
            ASIC's port
        user
            ASIC's username
        password
            ASIC's password
        """
        logging.info(f"Shutting down ASIC: {ip}:{port}")

        # Updating ASIC's status in DB
        self.update_asic_status(ip, online='False')
        # Restarting CGMiner
        self.restart_asic(ip, port, user, password)
        # Disabling internet access
        self.disable_internet_access(ip)

    def enable_asic(self, ip, port, user, password):
        """
        Enables an ASIC
        It's achieved via enabling internet access

        Parameters
        ----------
        ip
            ASIC's IP
        port
            ASIC's port
        user
            ASIC's username
        password
            ASIC's password
        """
        logging.info(f"Starting ASIC: {ip}:{port}")

        # Changing ASIC's status in DB
        self.update_asic_status(ip, online='True')
        # Enabling internet access
        self.enable_internet_access(ip)

    def disable_internet_access(self, ip):
        """
        Disables internet access for an ASIC
        Parameters
        ----------
        ip
            ASIC's IP
        """
        logging.info(f"Disabling internet access for: {ip}")

        try:
            # Connecting to Mikrotik's router
            api = self.get_routeros_api()

            # Adding IP to a blacklist (BL)
            list_address = api.get_resource('/ip/firewall/address-list')
            list_address.add(address=ip, list="BL")
        except Exception as e:
            logging.error(f"Error while disabling internet access for {ip}: {e}")

    def enable_internet_access(self, ip):
        """
        Enables internet access for an ASIC

        Parameters
        ----------
        ip
            ASIC's IP
        """
        logging.info(f"Enabling internet access for: {ip}")

        try:
            # Connecting to Mikrotik's router
            api = self.get_routeros_api()

            # Removing IP from a blacklist (IP)
            list_address = api.get_resource('/ip/firewall/address-list')
            rule_id = list_address.detailed_get(address=ip)[0]['id']
            list_address.remove(id=rule_id)
        except Exception as e:
            logging.error(f"Error while enabling internet access for {ip}: {e}")

    def flush_access_rules(self):
        """
        Deletes all internet access rules
        """
        logging.info("Flushing internet access rules")

        try:
            # Connecting to Mikrotik's router
            api = self.get_routeros_api()

            # Fetching all rules
            list_address = api.get_resource('/ip/firewall/address-list')
            rules = list_address.detailed_get()

            # Iterating through the list of rules
            for rule in rules:
                # Deleting a rule
                list_address.remove(id=rule['id'])
        except Exception as e:
            logging.error(f"Error while flushing internet access rules: {e}")

    def get_routeros_api(self):
        """
        Establishes a connecting with Mikrotik's router

        Returns
        -------
        api
            Access to Mikrotik's API
        """
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
        """
        Restarts CGMiner process

        Parameters
        ----------
        ip
            ASIC's IP
        port
            ASIC's port
        user
            ASIC's username
        password
            ASIC's password
        """
        logging.info(f"Restarting ASIC: {ip}:{port}")

        try:
            # Connecting to ASIC via API
            api = DragonAPI(f"{ip}:{port}",
                            username=user,
                            password=password,
                            timeout=self.reset_asic_timeout)

            # Restarting CGMiner
            api.restartCgMiner()
        except Exception as e:
            logging.error(f"Error occurred during restarting an ASIC ({ip}): {e}")

    @orm.db_session
    def show_status(self):
        """
        Returns a list of all ASICs

        Returns
        ----------
        hosts
            A list of all ASICs
        """
        # Fetching all hosts
        hosts = Hosts.select()

        if os.getenv('DEBUG'):
            for host in hosts:
                # Printing info about an ASIC
                logging.info([
                    host.id,
                    host.ip,
                    host.power,
                    host.power_group,
                    host.online]
                )

        return hosts

    @orm.db_session
    def write_logs(self, available_power, active_power):
        """
        Sends values of available and active power to InfluxDB

        Parameters
        ----------
        available_power
            A value of available power
        active_power
            A value of active power
        """
        try:
            # Establishing connection
            client = InfluxDBClient(
                url=f"http://{self.influxdb['host']}:{self.influxdb['port']}",
                token=self.influxdb['token'],
                org=self.influxdb['org']
            )

            # Write script
            write_api = client.write_api()

            # Creating a measurement for available power
            p = Point("power").tag("type", "available").field("power", available_power)
            write_api.write(bucket=self.influxdb['bucket'], org=self.influxdb['org'], record=p)

            # Creating a measurement for active power
            p = Point("power").tag("type", "active").field("power", active_power)
            write_api.write(bucket=self.influxdb['bucket'], org=self.influxdb['org'], record=p)

            for host in self.show_status():
                online = 1 if host.online == 'True' else 0
                p = Point("power").tag("type", host.ip).field("online", online)
                write_api.write(bucket=self.influxdb['bucket'], org=self.influxdb['org'], record=p)
        except Exception as e:
            logging.error(f"Error writing logs: {e}")


if __name__ == '__main__':
    # Time between checks
    SLEEP_TIMER = 15
    # Timeout for accessing ASIC
    RESET_ASIC_TIMEOUT = 5
    # Timeout for accessing Mikrotik router
    MIKROTIK_ACCESS_TIMEOUT = 5
    # URL for getting active power updates
    URL = "http://127.0.0.1:8000/power.json"
    # Router credentials
    ROUTER = {
        'ip': '192.168.88.1',
        'port': 8728,
        'username': 'admin',
        'password': 'aszpvo'
    }
    # InfluxDB credentials
    INFLUXDB = {
        'host': 'localhost',
        'port': 8086,
        'token': 'uctBdaWM6wYmVJoxn6NdYudLIZcYEnXoLgVKzy9iVrtcYqK305Krt4uMQO1CYeokvYXxaHpPErBWw8xamUAqHg==',
        'org': 'ASIC',
        'bucket': 'power'
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

    # Starting main loop
    AsicAgent().run()
