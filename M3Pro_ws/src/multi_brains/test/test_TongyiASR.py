import os
from ament_index_python.packages import get_package_share_directory
from multi_brains.utils.asr import Tongyi_ASR
import yaml
config_file=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','multi_brains_setting.yaml')
test_audio_zh=os.path.join(get_package_share_directory("multi_brains"),'system_vioce','test_zh.wav')
with open(config_file, "r") as file:
        config_param = yaml.safe_load(file)
asr_model=config_param.get("OLINE_ASR_MODEL","paraformer-realtime-v2")
sample_rate=config_param.get("SAMPLE_RATE",16000)
aliyun_api=config_param.get("ALIYUN_API_KEY","")

ASR_Engine=Tongyi_ASR(model_name=asr_model,sample_rate=sample_rate,API=aliyun_api)
if ASR_Engine.init_asr_engine():
    print("ASR init success")
else:
    print("ASR init failed")

res=ASR_Engine.recognize(test_audio_zh)
if res[0]:
    print(res[1])
else:
    print("Speech recognition failure")