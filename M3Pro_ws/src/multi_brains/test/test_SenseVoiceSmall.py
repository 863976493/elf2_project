
from multi_brains.utils.asr import SenseVoiceSmall_ASR
import os
from ament_index_python.packages import get_package_share_directory

model_path=os.path.join(os.path.expanduser('~'),'MODELS','asr','SenseVoiceSmall')
test_audio_zh=os.path.join(get_package_share_directory("multi_brains"),'system_vioce','test_zh.wav')
test_audio_en=os.path.join(get_package_share_directory("multi_brains"),'system_vioce','test_en.wav')
local_asr=SenseVoiceSmall_ASR(model_path=model_path,language="zh")
print("==========================================测试本地中文语音识别=================================================")
if local_asr.init_asr_engine(): print("SenseVoiceSmall ASR_Engine initialized successfully")
res=local_asr.recognize(test_audio_zh)
if res[0]:
    print(res[1])
else:
    print("Speech recognition failure")


local_asr=SenseVoiceSmall_ASR(model_path=model_path,language="en")
print("==============================Test local English speech recognition============================")
if local_asr.init_asr_engine(): print("SenseVoiceSmall ASR_Engine initialized successfully")
res=local_asr.recognize(test_audio_en)
if res[0]:
    print(res[1])
else:
    print("Speech recognition failure")