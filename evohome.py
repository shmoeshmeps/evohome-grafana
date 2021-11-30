import sys
try:
    from evohomeclient2 import EvohomeClient, AuthenticationError
except ModuleNotFoundError:
    sys.path.append("./evohome-client")
from influxdb import InfluxDBClient
from requests.exceptions import ConnectionError
import time
import os
import requests
import yaml
import datetime

import logging

ehlog = logging.getLogger(__name__)

# Have to set the level of a logger set in the evohomeclient lib to get any output
logging.getLogger().setLevel(logging.DEBUG)

def parse_hw_windows_from_file(filename:str):
    """
    Allows creation of simple hr/min style HW schedule from yaml
    :param filename:
    :return: hw_windows list of start and end hrs and mins
    """

    with open(filename,'r') as filey:
        parsed = yaml.safe_load(filey)

    hw_windows = []
    for window in parsed['windows']:
        startwin_hr, startwin_min = window['on'].values()
        endwin_hr, endwin_min = window['off'].values()
        hw_windows.append(
            {
                'startwin': datetime.datetime.now().replace(hour=startwin_hr, minute=startwin_min),
                'endwin': datetime.datetime.now().replace(hour=endwin_hr, minute=endwin_min),
            },
        )
        ehlog.debug(f'New window start {startwin_hr} hrs {startwin_min} mins')
        ehlog.debug(f'New window end {endwin_hr} hrs {endwin_min} mins')
    hw_setpoint = parsed['temp']
    ehlog.debug(f'HW temp set to {hw_setpoint}')
    return hw_windows, hw_setpoint

if __name__ == "__main__":

    ehlog.info("Script Starting")
    ehlog.debug("Fetching creds from env vars")
    username = os.environ['EH-USERNAME']
    password = os.environ['EH-PASSWORD']
    try:
        ehlog.debug("Attempting to fetch influxdb host from env var")
        influxdb_host = os.environ['INFLUXDB_HOST']
    except:
        influxdb_host = 'influxdb'  # For docker
        ehlog.debug("Failed to influxdb host from env vars, using default")

    ehlog.info(f'influxdb is {influxdb_host}')
    ehlog.info(f'username is {username}')

    db = "EH-TEMPS"

    try:
        ehlog.debug("Attempting to grab hotwater setpoint from env var")
        hotwater_setpoint_max = os.environ['HOTWATER_SETPOINT']
    except KeyError:
        hotwater_setpoint_max = 55.0  # Cannot be pulled from API to manually set
        ehlog.warning(f"Failed grabbing hotwater setpoint from env var, using local default val {hotwater_setpoint_max}")

    try:
        ehlog.debug(f"Grabbing poll interval from env var")
        poll_interval = os.environ['POLL_INTERVAL']
    except KeyError:
        poll_interval = 300.0
        ehlog.warning(f"Failed grabbing poll interval from env var, using local default val {poll_interval} seconds")

    hw_schedule_filename = "tmp/hotwater_schedule.yaml"


    starttime = time.time()
    all_ok = True
    # Connect to Influxdb
    ehlog.info("Connecting to InfluxDB")
    try:
        client = InfluxDBClient(host=influxdb_host, port=8086)
        ehlog.info(f"Connected. Checking existence of db {db}.")
        client.create_database(db)
        ehlog.info(f"Database {db} found (or created). Getting DB properties.")
        client.get_list_database()
    except Exception as e:
        ehlog.info(e)
        all_ok = False
        ehlog.info('Sleeping for 5 - that always helps...')
        time.sleep(poll_interval - ((time.time() - starttime) % poll_interval))
    # ehlog.info("Initialising evohome API")
    while all_ok:
        # Request evohome temperatures
        ehlog.info("Requesting temps from Evohome API")
        try:
            temps_data = list(eclient.temperatures())
        except NameError:
            ehlog.warning("Client API not yet initialised - initialising...")
            try:
                eclient = EvohomeClient({username}, {password})
            except AuthenticationError as e:
                if "invalid_grant" in str(e):
                    ehlog.error("invalid_grant when authenticating - check your credentials")
                    all_ok = False
                else:
                    if "attempt_limit_exceeded" in str(e):
                        ehlog.warning("attempt_limit_exceeded - will try sleeping 5 mins before container restart")
                    else:
                        ehlog.info(e)
                        ehlog.warning("API overload error - will try sleeping 5 mins before container restart")
                    time.sleep(300)
                all_ok = False
            else:
                ehlog.info("Evohome API Init successful")
                continue


        else: # We managed to get the temps from the API
            now = datetime.datetime.now()
            # hw_windows = set_hw_windows() # Updates windows to be the hours of today
            hotwater_setpoint = 0.0
            try:
                hw_windows, hotwater_setpoint_max = parse_hw_windows_from_file(hw_schedule_filename) # Updates windows to be the hours of today
            except Exception as e:
                ehlog.error(f"Failed to get hot water windows and setpoint from file. Setpoint set to {hotwater_setpoint}")
            else:
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
                ehlog.info([{"measurement": "Temperatures", "fields":device}])
                client.write_points([{"measurement":"Temperatures","fields":device}], database=db)
            global_heat = 1 if any([x['call_heat'] for x in temps_data]) else 0
            client.write_points([{"measurement":"Global","fields":{'call_heat':global_heat}}], database=db)
            ehlog.info(f"Global Heat: {global_heat}")


            # Collect and store OH temperatures
            if API_key := os.environ.get("OW-API-KEY"):
                ehlog.info("Collect and store outside weather temperatures")
                # API_key = os.environ['OW-API-KEY']
                city_name = os.environ['OW-CITY']
                base_url = "http://api.openweathermap.org/data/2.5/weather?"
                Final_url = base_url + "q=" + city_name + "&appid=" + API_key + "&units=metric"
                weather_data = requests.get(Final_url).json()
                try:
                    temp = weather_data['main']['temp']
                    ehlog.info([{"measurement": "ext-Temperatures", "fields":{'ext-temp': temp}}])
                    client.write_points([{"measurement":"ext-Temperatures","fields":{'ext-temp': temp}}], database=db)
                except KeyError:
                    ehlog.info('Issue getting Outwide Weather data - ignoring this attempt.')
                    ehlog.info(weather_data)

                

            # Inform Healthchecks.io
            if healthchecks := os.environ.get("HEALTHCHECKS-IO"):
                # healthchecks = os.environ['HEALTHCHECKS-IO']
                requests.get(healthchecks)

        # except ConnectionError as e:
        #     ehlog.info("No Database Connection")
        # except AuthenticationError as e:
        #     ehlog.info("API overload error - sleeping 5 mins before retry")
        time.sleep(poll_interval - ((time.time() - starttime) % poll_interval))

    ehlog.info('Exiting script')
