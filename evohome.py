from evohomeclient2 import EvohomeClient, AuthenticationError
from influxdb import InfluxDBClient
from requests.exceptions import ConnectionError
import time
import os
import requests
import datetime

print("Starting")
username = os.environ['EH-USERNAME']
password = os.environ['EH-PASSWORD']
influxdb_host = os.environ['INFLUXDB_HOST']

# influxdb_host = 'influxdb' # For docker
# influxdb_host = 'localhost' # For local

db = "EH-TEMPS"
hotwater_setpoint_max = 55.0 # Cannot be pulled from API to manually set
poll_interval = 300.0

# Basic hot water manual schedule for setpoints

def set_hw_windows():
    """
    A simple way to manually set the hot water activation windows within the script for now.
    :return:
    """
    hw_windows = [
        {
            'startwin' : datetime.datetime.now().replace(hour=6),
            'endwin' : datetime.datetime.now().replace(hour=8),
        },
        {
            'startwin': datetime.datetime.now().replace(hour=16,minute=30),
            'endwin': datetime.datetime.now().replace(hour=18,minute=30),
        },
    ]
    return hw_windows




starttime = time.time()
if __name__ == "__main__":
    all_ok = True
    # Connect to Influxdb
    print("Connecting to InfluxDB")
    client = InfluxDBClient(host=influxdb_host, port=8086)
    print(f"Connected. Checking existence of db {db}.")
    client.create_database(db)
    print(f"Database {db} found (or created). Getting DB properties.")
    client.get_list_database()
    # print("Initialising evohome API")
    while all_ok:
        # Request evohome temperatures
        print("Collect and store evohome temperatures")
        try:
            temps_data = list(eclient.temperatures())

        except NameError:
            print("Client API not yet initialised - initialising...")
            try:
                eclient = EvohomeClient({username}, {password})
            except AuthenticationError as e:
                if "invalid_grant" in e:
                    print("invalid_grant when authenticating - check your credentials")
                    all_ok = False
                else:
                    if "attempt_limit_exceeded" in e:
                        print("attempt_limit_exceeded - will try sleeping 5 mins before container restart")
                    else:
                        print(e)
                        print("API overload error - will try sleeping 5 mins before container restart")
                    time.sleep(300)
                all_ok = False
            else:
                print("Init successful")
                continue


        else: # We managed to get the temps from the API
            now = datetime.datetime.now()
            hw_windows = set_hw_windows() # Updates windows to be the hours of today
            hotwater_setpoint = 0.0
            for hw_window in hw_windows:
                if now > hw_window['startwin'] and now < hw_window['endwin']:
                    hotwater_setpoint = hotwater_setpoint_max
            for device in temps_data:

                # Hot Water has null strings for name and setpoint so manually add them
                if device['thermostat'] == "DOMESTIC_HOT_WATER":
                    device['setpoint'] = hotwater_setpoint
                    device['name'] = "Hot Water"
                # Manually add in a flag for call_heat
                device['call_heat'] = 1 if device['setpoint'] > device['temp'] else 0
                #TODO: Need to figure out how to deal with hot water call - use schedule
                print([{"measurement":"Temperatures","fields":device}])
                client.write_points([{"measurement":"Temperatures","fields":device}], database=db)
            global_heat = 1 if any([x['call_heat'] for x in temps_data]) else 0
            client.write_points([{"measurement":"Global","fields":{'call_heat':global_heat}}], database=db)
            print(f"Global Heat: {global_heat}")


            # Collect and store OH temperatures
            if "OW" in os.environ:
                print("Collect and store outside weather temperatures")
                API_key = os.environ['OW-API-KEY']
                city_name = os.environ['OW-CITY']
                base_url = "http://api.openweathermap.org/data/2.5/weather?"
                Final_url = base_url + "q=" + city_name + "&appid=" + API_key + "&units=metric"
                weather_data = requests.get(Final_url).json()
                temp = weather_data['main']['temp']
                print([{"measurement":"ext-Temperatures","fields":{'ext-temp': temp}}])
                client.write_points([{"measurement":"ext-Temperatures","fields":{'ext-temp': temp}}], database=db)

            # Inform Healthchecks.io
            if "HEALTHCHECKS-IO" in os.environ:
                healthchecks = os.environ['HEALTHCHECKS-IO']
                requests.get(healthchecks)

        # except ConnectionError as e:
        #     print("No Database Connection")
        # except AuthenticationError as e:
        #     print("API overload error - sleeping 5 mins before retry")
        time.sleep(poll_interval - ((time.time() - starttime) % poll_interval))

"""
If a TRV reaches it's setpoint, does it fully close or partially close?
Does it then stop the boiler call?
How does the boiler overrun thing play into this?
"""