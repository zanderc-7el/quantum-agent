from typing import (Literal, Tuple, Union, Protocol, Generic, TypeVar)
import typing as T

class Node:
    """
    抽象语法树节点
    Args:
        type (str): 节点名
        children (T.List[T.Any], optional): 子节点列表. Defaults to None.
        leaf (T.Any, optional): 节点中的一些常量值. Defaults to None.
        pos (_type_, optional): 节点对应语句所在原文位置（行号）. Defaults to None.
    """
    def __init__(self,type: str,children: T.List[T.Any]=None,leaf: T.Any=None , pos=None):
        self.type = type
        if children:
            self.children = children
        else:
            self.children = [ ]
        self.leaf = leaf
        self.pos=pos
        self.val = None
