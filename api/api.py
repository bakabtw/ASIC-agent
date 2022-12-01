from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from datetime import datetime
from pony import orm

app = FastAPI()

app.state.available_power = 0


@app.get("/")
async def root():
    return HTMLResponse(
        content=''.join(
            map(str, open('template.html').readlines())
        ),
        status_code=200
    )


@app.get("/get_power")
async def get_power():
    return {
        'success': True,
        'time': datetime.now(),
        'power': app.state.available_power
    }


@app.post("/set_power/{power}")
async def set_power(power: int):
    app.state.available_power = power

    return {
        'success': True
    }


@app.get("/asic_status")
async def asic_status():
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

    db.disconnect()

    return output
