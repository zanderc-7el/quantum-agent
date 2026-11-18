class GlobalSettingMeta(type):
    """属性元类"""
    
    def __new__(cls, name, bases, attrs):
        # 收集所有类属性（排除特殊方法和元类添加的方法）
        class_attrs = {}
        for attr_name, attr_value in attrs.items():
            if not attr_name.startswith('__') or attr_name.endswith('__'):
                class_attrs[attr_name] = attr_value
        
        # 为每个类属性创建getter和setter
        for attr_name, default_value in class_attrs.items():
            if not callable(default_value):  # 只对非方法属性处理
                private_attr = f"__{attr_name}"
                
                # 设置初始值
                attrs[private_attr] = default_value
                
                # 创建getter
                def getter(cls, attr=private_attr):
                    return getattr(cls, attr)
                attrs[f"get_{attr_name}"] = classmethod(getter)
                
                # 创建setter
                def setter(cls, value, attr=private_attr):
                    setattr(cls, attr, value)
                attrs[f"set_{attr_name}"] = classmethod(setter)
                
                # 移除原始属性定义
                if attr_name in attrs:
                    del attrs[attr_name]
        
        return super().__new__(cls, name, bases, attrs)

class Global_Setting(metaclass=GlobalSettingMeta):
    """
    全局参数
        只需定义属性，元类会自动生成getter/setter
    """
    datapath: str = './result'
    logpath: str = './logs'
    log_output: str = 'stdout'#'stdout' # stdout/file
    log_level: str = 'info'
    setting_path: str = "./config/settings.json"
    
    #debug_mode: bool = False
    #db_url: str = 'sqlite:///app.db'
    #setting_file: str = '/Users/huazhelou/Documents/quantum/gitlab/scctrl/settings.json'
    #experiment_dir: str = '/Users/huazhelou/Documents/quantum/gitlab/scctrl/exp_dir/'
    #default_exp_file: str = 'default.json'
    #pic_dir = "/Users/huazhelou/Documents/quantum/gitlab/scctrl/pic/"
    #pic_url: str = '192.168.1.39:8080/files/'
    #algo_code_dir = "/Users/huazhelou/Documents/quantum/gitlab/scctrl/algo_code/"
    #algo_detail_dir = "/Users/huazhelou/Documents/quantum/gitlab/scctrl/algo_detail/"
    #algo_url: str = '192.168.1.39:8080/details/'
    
    