from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from datetime import datetime
from pony import orm
import uvicorn
from dragon_rest.dragons import DragonAPI
from concurrent.futures import ThreadPoolExecutor
from cachetools import cached, TTLCache
import json

app = FastAPI()

app.state.available_power = 0
app.state.monitoring = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


db.generate_mapping()


@app.get("/get_power", description="Returns available power (in Watts) for ASICs")
async def get_power():
    return {
        'success': True,
        'time': datetime.now(),
        'power': app.state.available_power
    }


@app.post("/set_power/{power}", description="Sets available power (in Watts) for ASIC-agent")
async def set_power(power: int):
    app.state.available_power = power

    return {'success': True}


@app.get("/get_asic/{asic_id}", description="Returns info from DB about an ASIC")
async def get_asic(asic_id: int):
    with orm.db_session:
        host = Hosts.get(id=asic_id)

    if not host:
        return {'detail': 'Not Found'}

    return {
        'id': host.id,
        'ip': host.ip,
        'port': host.port,
        'user': host.user,
        'password': host.password,
        'type': host.type,
        'power': host.power,
        'phase': host.phase,
        'power_group': host.power_group
    }


@app.post("/update_asic", description="Updates info about an ASIC in DB")
async def update_asic(request: Request):
    with orm.db_session:
        data = await request.json()
        host = Hosts.get(id=data['id'])

        # Creating new entry
        if not host:
            asic = Hosts(
                ip=data['ip'],
                port=data['port'],
                user=data['user'],
                password=data['password'],
                type=data['type'],
                power=data['power'],
                phase=data['phase'],
                power_group=data['power_group'],
                online='false'
            )

            return {'success': 'true', 'status': 'created'}
        # Updating existing entry
        else:
            host.ip = data['ip']
            host.port = data['port']
            host.user = data['user']
            host.password = data['password']
            host.type = data['type']
            host.power = data['power']
            host.phase = data['phase']
            host.power_group = data['power_group']

            return {'success': 'true', 'status': 'updated'}


@app.post("/delete_asic/{asic_id}", description="Deletes an ASIC from DB")
async def delete_asic(asic_id: int):
    with orm.db_session:
        host = Hosts.get(id=asic_id)

        if not host:
            return {'detail': 'Not Found'}
        else:
            host.delete()

            return {'success': 'true', 'status': 'updated'}


@app.get("/asic_status", description="Returns info about all ASICS from DB (credentials, status)")
async def asic_status():
    with orm.db_session:
        hosts = Hosts.select()
        output = []

        for host in hosts:
            output.append(
                {
                    'id': host.id,
                    'ip': host.ip,
                    'port': host.port,
                    'power': host.power,
                    'phase': host.phase,
                    'power_group': host.power_group,
                    'online': host.online
                }
            )

    return output


@app.get("/get_active_power", description="Returns power (in Watts) used by all ASICs (calculated value)")
async def get_active_power():
    with orm.db_session:
        hosts = Hosts.select()
        power = 0

        for host in hosts:
            power += host.power if host.online.lower() == 'true' else 0

    return {
        'success': True,
        'time': datetime.now(),
        'power': power
    }


@app.get("/get_asic_info/{asic_id}", include_in_schema=False, description="Returns an info about an ASIC from API")
@cached(cache=TTLCache(maxsize=1024, ttl=60))
def get_asic_info(asic_id: int):
    with orm.db_session:
        host = Hosts.get(id=asic_id)

    if not host:
        return {'detail': 'Not Found'}

    try:
        # Connecting to ASIC via API
        api = DragonAPI(f"{host.ip}:{host.port}",
                        username=host.user, password=host.password,
                        timeout=5)

        r = api.summary()
    except Exception:
        r = {'success': False, 'error': 'Cannot connect to the ASIC'}

    # Adding ASIC id to response
    r['id'] = asic_id

    return r


@app.get("/asics_info", include_in_schema=False, description="Returns info about all ASICS from API")
def asics_info():
    return PlainTextResponse(fetch_asics_info())


@app.get("/asics_temp", description="Return info about temperature of each hashing board")
def asics_temp():
    # Fetching data from ASICS
    fields = json.loads(fetch_asics_info())
    r = []

    # Iterating through ASICs
    for field in fields:
        # Checking if there's a response from ASIC
        if field['success'] is False:
            continue

        asic_id = field['id']
        temperature = []

        # Iterating through boards
        for dev in field['DEVS']:
            temperature.append({
                'board_id': dev['ID'],
                'temperature': dev['Temperature']
            })

        # Adding data to response
        r.append({
            'id': asic_id,
            'temperature': temperature
        })

    return r


@app.get("/get_power_by_hashrate", description="Returns power (in Watts) used by all ASICs based on total hashrate")
def get_power_by_hashrate():
    # Fetching data from ASICS
    fields = json.loads(fetch_asics_info())
    hashrate = 0.0

    # Iterating through ASICs
    for field in fields:
        # Checking if there's a response from ASIC
        if 'success' not in field or field['success'] is False:
            continue

        if 'TotalHash' in field and field['TotalHash']['Unit'] == 'TH/s':
            hashrate += field['TotalHash']['Hash Rate']

    return {
        'success': True,
        'time': datetime.now(),
        'power': round(hashrate * 100),
        'hashrate': round(hashrate, 1)
    }


@app.post("/monitoring", description="Updates info about container monitoring (temp, voltage, etc)")
async def update_monitoring(request: Request):
    data = await request.json()
    app.state.monitoring = data

    return {
        'success': True,
        'time': datetime.now(),
    }


@app.get("/monitoring", description="Returns info about container monitoring")
async def get_monitoring():
    return app.state.monitoring


@app.get("/running_asics", description="Returns a number of running (mining) ASICs")
async def running_asics():
    # Fetching data from ASICS
    fields = json.loads(fetch_asics_info())
    count = 0

    # Iterating through ASICs
    for field in fields:
        # Checking if there's a response from ASIC
        if 'success' not in field or field['success'] is False:
            continue

        if 'TotalHash' in field and field['TotalHash']['Unit'] == 'TH/s':
            count += 1

    return {
        'success': True,
        'running_asics': count
    }


@app.get("/next_step_readiness", description="Returns active power (in Watts) when ASICs "
                                             "for the current step has started or 0 if they're starting up")
async def next_step_readiness():
    # Fetching data from ASICS
    fields = json.loads(fetch_asics_info())
    mining_count = 0

    # Iterating through ASICs
    for field in fields:
        # Checking if there's a response from ASIC
        if 'success' not in field or field['success'] is False:
            continue

        if 'TotalHash' in field and field['TotalHash']['Unit'] == 'TH/s':
            mining_count += 1

    # Fetching data from a table of
    fields = await asic_status()
    active_count = 0

    # Iterating through ASICs
    for field in fields:
        if 'online' in field and field['online'] == 'True':
            active_count += 1

    if mining_count < active_count:
        power = 0
    else:
        power = 1000 * (app.state.monitoring['meter']['power']['A'] + app.state.monitoring['meter']['power']['B'] +
                        app.state.monitoring['meter']['power']['C'])

    return {
        "power": power,
        "mining_count": mining_count,
        "active_count": active_count
    }


def fetch_asics_info():
    with orm.db_session:
        hosts = Hosts.select()
        pool = ThreadPoolExecutor(max_workers=36)
        ids = []

        for host in hosts:
            ids.append(host.id)

    responses = pool.map(get_asic_info, ids)
    pool.shutdown()

    # Fetching all data from generator
    results = [x for x in responses]

    # Dumping JSON
    return json.dumps(results)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
