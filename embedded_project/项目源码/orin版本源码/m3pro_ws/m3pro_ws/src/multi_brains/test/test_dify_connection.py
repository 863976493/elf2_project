from multi_brains.utils.dify_client import ChatClient
import yaml
from ament_index_python.packages import get_package_share_directory
import os
import requests
from colorama import Fore
config_file=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','multi_brains_setting.yaml')
print (config_file)

with open(config_file, "r") as file:
        config_param = yaml.safe_load(file)
dify_base_url=config_param.get("DIFY_BASE_URL")
dify_api_key=config_param.get("DIFY_API_KEY")
client = ChatClient(dify_api_key, dify_base_url)

try:
    response = client.get_meta("yahboom")
    if response.status_code == 200:
        print(Fore.GREEN+f"Connection dify  successful"+Fore.RESET)
    else:
        print(Fore.RED+f"Connection dify  failed with status code: {response.status_code}"+Fore.RESET)
except requests.exceptions.RequestException as e:

    print(Fore.RED+f"Connection error: {str(e)}"+Fore.RESET)
except Exception as e:

    print(Fore.RED+f"Unexpected error: {str(e)}"+Fore.RESET)