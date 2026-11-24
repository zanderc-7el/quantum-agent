class QuantBase:
    def __init__(self, name, value=None, unit=None,
                 ch=None, set_cmd=None, get_cmd=None):
        self.name = name
        self.value = value
        self.unit = unit
        self.ch = ch
        self.set_cmd = set_cmd
        self.get_cmd = get_cmd

    def format_set_cmd(self, value, **kw):
        raise NotImplementedError

    def format_get_cmd(self):
        raise NotImplementedError

    def parse_get_value(self, raw):
        return raw
    
class QReal(QuantBase):
    def __init__(self, name, value=None, unit=None,
                 ch=None, set_cmd=None, get_cmd=None):
        super().__init__(name, value, unit, ch, set_cmd, get_cmd)

    def format_set_cmd(self, value, **kw):
        data = {
            "value": float(value),
            "unit": kw.get("unit", self.unit),
            "ch": kw.get("ch", self.ch),
        }
        return self.set_cmd % data
    
    def format_get_cmd(self, **kw):
        data = {
            "value": float(self.value),
            "unit": kw.get("unit", self.unit),
            "ch": kw.get("ch", self.ch),
        }
        return self.get_cmd % data

    def parse_get_value(self, raw):
        return float(raw)

class QInteger(QReal):
    def format_set_cmd(self, value, **kw):
        value = int(value)
        return super().format_set_cmd(value, **kw)

    def format_get_cmd(self, **kw):
        return super().format_get_cmd(**kw)
    
    def parse_get_value(self, raw):
        return int(raw)
    

class QOption(QuantBase):
    def __init__(self, name, value, options, **kw):
        super().__init__(name, value=value, **kw)
        self.options = dict(options)

    def format_set_cmd(self, value, **kw):
        if value not in self.options:
            raise ValueError(
                f"{value} not in {list(self.options.keys())}"
            )
        return self.set_cmd % {
            "option": self.options[value],
            "ch": kw.get("ch", self.ch),
        }
    
    def format_get_cmd(self, **kw):
        data = {
            "option": self.options[self.value],
            "ch": kw.get("ch", self.ch),
        }
        return self.get_cmd % data

    def parse_get_value(self, raw):
        for k, v in self.options.items():
            if v == raw.strip():
                return k
        return raw
    
class QVector(QuantBase):
    def __init__(self, name, **kw):
        super().__init__(name, **kw)

    def format_set_cmd(self, value, **kw):
        raise RuntimeError("QVector is read-only")
    