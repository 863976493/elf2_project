from multi_brains.utils.asr import XUNFEI_ASR
import os
from colorama import Fore
import yaml
from ament_index_python.packages import get_package_share_directory
#-------------------------------------------config file path-------------------------------------------------
config_file=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','multi_brains_setting.yaml')
test_audio_en=os.path.join(get_package_share_directory("multi_brains"),'system_vioce','test_en.wav')
#------------------------------------------------------------------------------------------------------------

print(" =======================test Xunfei XUNFEI_ASR =============================")
with open(config_file, "r") as file:
    config_param = yaml.safe_load(file)
key_path=os.path.join(os.path.expanduser('~'),config_param.get("XUNFEI_KEY",""))
xunfei_encrypted_key=os.path.join(os.path.expanduser('~'),config_param.get("XUNFEI_ENCRYPTED",""))
asr_engine=XUNFEI_ASR(key_path,xunfei_encrypted_key)

if asr_engine.init_asr_engine():
    print(Fore.BLUE+"Xunfei ASR_Engine initialized successfully"+Fore.RESET)

res=asr_engine.recognize(test_audio_en)
print(res[1])