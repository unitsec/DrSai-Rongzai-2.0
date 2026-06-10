# GSAS-II 空间群编号与符号参考

目的：为智能体提供“编号-符号”一致性检查的固定依据，避免直接信任自由生成的符号。

## 权威来源

1. GSAS-II 编号到符号映射（1-230）
   - 文件：tools/GSAS-II-master/GSASII/GSASIIspc.py
   - 对象：spgbyNum

2. GSAS-II 可接受符号列表（按晶系/Laue 分组）
   - 文件：tools/GSAS-II-master/GSASII/GSASIIspc.py
   - 对象：spglist

3. 符号标准化
   - 文件：tools/GSAS-II-master/GSASII/GSASIIspc.py
   - 函数：StandardizeSpcName

4. 符号转编号
   - 文件：tools/GSAS-II-master/GSASII/GSASIIspc.py
   - 函数：SpaceGroupNumber

## 智能体执行规则（必须遵守）

1. 用户给出空间群时，优先要求或优先使用编号（1-230）。
2. 若仅有符号，先标准化，再求编号。
3. 若同时给出编号和符号，必须做一致性检查：
   - 计算符号对应编号（SpaceGroupNumber）
   - 与用户编号比较
   - 不一致时，禁止直接执行精修，先反馈冲突并要求确认
4. 对于不在标准编号映射内的非标准写法，只能在用户明确确认后使用。

## 建议输出格式（给上游调用）

当做一致性检查时，建议返回：
- input_number
- input_symbol
- normalized_symbol
- resolved_number_from_symbol
- expected_symbol_from_number
- match（true 或 false）
- reason

## 常见编号示例

- 1 -> P 1
- 2 -> P -1
- 14 -> P 21/c
- 62 -> P n m a
- 139 -> I 4/m m m
- 167 -> R -3 c
- 194 -> P 63/m m c
- 225 -> F m -3 m
