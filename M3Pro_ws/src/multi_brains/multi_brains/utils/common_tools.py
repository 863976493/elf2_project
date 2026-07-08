import re
import json
import psutil
import os
import yaml


def extract_json_content(
    raw_content,
):  # 解析变量提取json / Extract JSON by parsing variables
    try:
        # 方法一：分割代码块 / Method 1: Split code blocks
        if "```json" in raw_content:
            # 分割代码块并取中间部分 / Split code blocks and take the middle part
            json_str = raw_content.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_content:
            # 处理没有指定类型的代码块 / Handle code blocks without specified types
            json_str = raw_content.split("```")[1].strip()
        else:
            # 直接尝试解析 / Try parsing directly
            json_str = raw_content

        # 方法二：正则表达式提取（备用方案） / Method 2: Regular expression extraction (backup plan)
        if not json_str:

            match = re.search(r"\{.*\}", raw_content, re.DOTALL)
            if match:
                json_str = match.group()

        return json.loads(json_str)

    except Exception as e:
        return None
    

def kill_process_tree(pid):
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except psutil.NoSuchProcess:
        pass


class LogTranslator:
    """
    日志翻译类 / Log Translation Class
    """
    def __init__(self, language='zh'):
        
        self.language = language
        self.translations = {}
    def load_translations_file(self,language_dir:str)->bool:
        '''加载翻译文件 / Load translation file'''
        lang_file = os.path.join(language_dir, f"{self.language}.yaml")
        try:
            with open(lang_file, 'r', encoding='utf-8') as f:
                self.translations = yaml.safe_load(f)
            return True
        except :
            return False
    
    def get_text(self, key:str, **kwargs)->str:
        '''获取翻译 / Get translation'''
        text = self.translations.get(key)
        try:
            return text.format(**kwargs)
        except KeyError:
            print("The translation key cannot be found. Please check if the language configuration file has been accidentally modified")
            return text
        
