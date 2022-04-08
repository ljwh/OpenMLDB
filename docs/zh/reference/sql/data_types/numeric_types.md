# 数值类型

OpenMLDB支持布尔类型：

| 类型 | 大小   | 范围（有符号） | 范围（无符号） | 用途     |
| :--- | :----- | :------------- | :------------- | :------- |
| BOOL | 1 byte | (-128，127)    | (0，255)       | 小整数值 |

## 整数类型（精确值）

OpenMLDB支持种整数类型：`INT`, `SMALLINT`,`BIGINT`

| 类型            | 大小    | 范围（有符号）                                          | 范围（无符号）                  | 用途       |
| :-------------- | :------ | :------------------------------------------------------ | :------------------------------ | :--------- |
| SMALLINT或INT16 | 2 bytes | (-32 768，32 767)                                       | (0，65 535)                     | 小整数值   |
| INT或INT32      | 4 bytes | (-2 147 483 648，2 147 483 647)                         | (0，4 294 967 295)              | 大整数值   |
| BIGINT或INT64   | 8 bytes | (-9,223,372,036,854,775,808，9 223 372 036 854 775 807) | (0，18 446 744 073 709 551 615) | 极大整数值 |

## 浮点类型（近似值）

OpenMLDB支持两种浮点类型：`FLOAT`, `DOUBLE`

| 类型   | 大小    | 范围（有符号）                                               | 范围（无符号）                                               | 用途            |
| :----- | :------ | :----------------------------------------------------------- | :----------------------------------------------------------- | :-------------- |
| FLOAT  | 4 bytes | (-3.402 823 466 E+38，-1.175 494 351 E-38)，0，(1.175 494 351 E-38，3.402 823 466 351 E+38) | 0，(1.175 494 351 E-38，3.402 823 466 E+38)                  | 单精度 浮点数值 |
| DOUBLE | 8 bytes | (-1.797 693 134 862 315 7 E+308，-2.225 073 858 507 201 4 E-308)，0，(2.225 073 858 507 201 4 E-308，1.797 693 134 862 315 7 E+308) | 0，(2.225 073 858 507 201 4 E-308，1.797 693 134 862 315 7 E+308) | 双精度 浮点数值 |