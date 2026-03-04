# from typing import List, Dict, Any
import os
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from openai import OpenAI
import matplotlib.pyplot as plt
import numpy as np
from diffpy.pdffit2 import PdfFit
import json



app = FastAPI(title="PDF Refinement API")

import dotenv
here = os.path.abspath(os.path.dirname(__file__))
dotenv.load_dotenv(dotenv_path=os.path.join(here, '..','.env'))

client = OpenAI(
    api_key=os.environ.get("HEPAI_API_KEY"),
    base_url="https://aiapi.ihep.ac.cn/apiv2"
)

def upload_file(file_path: str, purpose: str = "user_data") -> str:
    """上传文件到图床接口
    Args:
        file_path (str): 文件路径
        purpose (str): 上传用途，默认为"user_data"

    Returns:
        str: 上传后的URL
    """
    file_obj = client.files.create(
            file=open(file_path, "rb"),
            purpose=purpose
        )
    return f"https://aiapi.ihep.ac.cn/apiv2/files/{file_obj.id}/preview"

def calcu_byte_size(file_path: str) -> int:
    """计算文件字节大小
    Args:
        file_path (str): 文件路径

    Returns:
        int: 文件字节大小
    """
    return os.path.getsize(file_path)

class PDFRefinementRequest(BaseModel):
    data_file: str
    structure_file: str
    qmax: float
    qdamp: float
    rmin: float
    rmax: float
    pdf_source: str = "X"
    thread_id: str = "anonymous"

@app.post("/api/pdf_refinement", tags=["PDF"])
def perform_pdf_refinement(request: PDFRefinementRequest) -> str:
        # data_file: str, 
        # structure_file: str, 
        # qmax: float, 
        # qdamp: float, 
        # rmin: float, 
        # rmax: float,
        # pdf_source: str = "X", 
        # thread_id: str = "anonymous"):
    """执行PDF精修分析
    
    Args:
        data_file: 实验PDF数据文件的绝对路径
        structure_file: 结构文件的绝对路径
        qmax: Q最大值(1/Å)
        qdamp: Q分辨率因子
        rmin: 精修最小r值(Å)
        rmax: 精修最大r值(Å)
        pdf_source: PDF数据来源，"X"或"N"。 X表示X-ray获取的PDF，N表示中子谱获取的PDF
        thread_id: 前端用户的请求ID，用于记录日志，固定传入，无需考虑
        
    Returns:
        json.dumps(result): 精修结果的JSON字符串，包含精修参数、输出文件信息、日志等信息
    """
    data_file = request.data_file
    structure_file = request.structure_file
    qmax = request.qmax
    qdamp = request.qdamp
    rmin = request.rmin
    rmax = request.rmax
    pdf_source = request.pdf_source
    thread_id = request.thread_id

    # result = {
    #     "status": "success",
    #     "output_files": [],
    #     "refined_params": {}
    # }
    
    try:
        # 验证输入文件是否存在
        if not os.path.exists(data_file):
            raise FileNotFoundError(f"数据文件不存在: {data_file}")
        if not os.path.exists(structure_file):
            raise FileNotFoundError(f"结构文件不存在: {structure_file}")

        # 创建PDF计算器对象
        pf = PdfFit()

        # 加载数据
        pf.read_data(data_file, pdf_source, qmax, qdamp)
        pf.read_struct(structure_file)

        # 配置精修参数
        # 晶格参数约束 (a=b=c)
        pf.constrain(pf.lat(1), "@1")
        pf.constrain(pf.lat(2), "@1")
        pf.constrain(pf.lat(3), "@1")
        pf.setpar(1, pf.lat(1))

        # 其他参数约束
        pf.constrain("pscale", "@20 * 2")
        pf.setpar(20, pf.getvar(pf.pscale) / 2.0)
        pf.constrain(pf.qdamp, "@21")
        pf.setpar(21, 0.03)
        pf.constrain(pf.delta2, 22)
        pf.setpar(22, 0.0003)

        # 温度因子约束
        for idx in range(1, 5):
            pf.constrain(pf.u11(idx), "@4")
            pf.constrain(pf.u22(idx), "@4")
            pf.constrain(pf.u33(idx), "@4")
        pf.setpar(4, pf.u11(1))

        # 执行精修
        pf.pdfrange(1, rmin, rmax)
        pf.refine()

        # 生成输出文件名
        output_dir = os.path.dirname(data_file)
        output_dir = os.path.join(output_dir, f"refined_{thread_id}")
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        output = os.path.join(output_dir, os.path.splitext(os.path.basename(data_file))[0])
        output_files = {
            "pdf": f"{output}.fgr",
            "structure": f"{output}.rstr",
            "results": f"{output}.res",
            "plot": f"{output}.png"
        }

        # 保存结果文件
        pf.save_pdf(1, output_files["pdf"])
        pf.save_struct(1, output_files["structure"])
        pf.save_res(output_files["results"])

        # 保存结果图表
        r = pf.getR()
        Gobs = pf.getpdf_obs()
        Gfit = pf.getpdf_fit()
        Gdiff = np.array(Gobs) - np.array(Gfit)
        Gdiff_baseline = -10

        plt.figure(figsize=(8, 6))
        plt.plot(r, Gobs, "ko", markersize=3, label="Experimental")
        plt.plot(r, Gfit, "b-", linewidth=1.5, label="Fit")
        plt.plot(r, Gdiff + Gdiff_baseline, "r-", linewidth=1, label="Difference")
        
        plt.xlabel("r (Å)", fontsize=12)
        plt.ylabel("G (Å$^{-2}$)", fontsize=12)
        plt.title("PDF Refinement Results", fontsize=14)
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_files["plot"], dpi=300)
        plt.close()

        # 收集精修参数
        result = {}
        # result["datasetId"] = uuid.uuid4().hex
        result["name"] = os.path.splitext(os.path.basename(data_file))[0]+f"_pdf_refined_qmax-{qmax}_qdamp-{qdamp}_rmin-{rmin}_rmax-{rmax}"
        result["data_type"] = 2
        refined_params= {
            "lattice_parameter": pf.getpar(1),
            "pscale": pf.getpar(20) * 2,
            "qdamp": pf.getpar(21),
            "delta2": pf.getpar(22),
            "temperature_factor": pf.getpar(4),
            "rw": pf.getrw()  # 精修权重因子R
        }
        refined_params_str = "使用diffpy.pdffit2进行精修。\nThe refined parameters are as follows:\n"
        for key in refined_params:
            refined_params_str += f"{key}: {refined_params[key]}\n"
        result["summary"] = refined_params_str
        result["researchField"] = "X-ray pdf, pair distribution function"
        total_size = 0
        files =[]
        for file_name in output_files:
            file_url = upload_file(output_files[file_name])
            file_size = calcu_byte_size(output_files[file_name])
            files.append({
                "name": os.path.basename(output_files[file_name]),
                "url": file_url,
                "summary": "PDF精修结果文件.",
                "size": file_size,
                "filePath": output_files[file_name]
            })
            total_size += file_size
        # 记录输入的文件信息
        file_url = upload_file(data_file)
        file_size = calcu_byte_size(data_file)
        files.append({
                "name": os.path.basename(data_file),
                "url": file_url,
                "summary": "PDF数据文件.",
                "size": file_size,
                "filePath": data_file
            })
        total_size += file_size
        file_url = upload_file(structure_file)
        file_size = calcu_byte_size(structure_file)
        files.append({
                "name": os.path.basename(structure_file),
                "url": file_url,
                "summary": "PDF结构文件.",
                "size": file_size,
                "filePath": structure_file
            })
        total_size += file_size

        result["files"] = files
        result["size"] = total_size

    except FileNotFoundError as e:
        result = {
            "status": "error",
            "error": str(e),
            "suggestion": "请检查文件路径是否正确"
        }
    except Exception as e:
        result = {
            "status": "error",
            "error": f"精修过程中出错: {str(e)}",
            "suggestion": "请检查输入参数或联系管理员"
        }

    return json.dumps(result)

if __name__ == "__main__":
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=int(os.getenv("PDF_API_PORT", 8085)),  # 读取环境变量PORT或默认8000
        log_level="info"
    )