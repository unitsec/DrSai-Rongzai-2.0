import multiprocessing
from pathlib import Path
import logging
import os
from glob import glob
from mcp.server.fastmcp import FastMCP

mcp=FastMCP(
    name="GSASII_Refinement_Tool",
    instructions="A tool for refining neutron diffraction data and generating refined structure files.",
    port = 42502, # for transport = "sse" to start a server-sent events (SSE) server
    )

def setup_logging(output_dir):
    """配置日志系统"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(Path(output_dir) / 'refinement.log'),
            logging.StreamHandler()
        ]
            )
    return logging.getLogger(__name__)

id_to_task={}
id_to_result={}
@mcp.tool()
def refine_data(data_path: str = None,
                 shape: str = None, 
                 para: dict = None, 
                 task_id: str = None) -> dict:
    id_=task_id
    if id_ not in id_to_task or id_to_task[id_]==None:
        # 任务创建
        id_to_result[id_]=multiprocessing.Queue()
        id_to_task[id_]=multiprocessing.Process(target=neutron_refinement,kwargs={
                    'output_dir': output_dir,
                    'neutron_data': gsa_path,
                    'neutron_inst': instprm_path,
                    'cif_files': cif_paths,
                    'gpx_file': gpx_path,
                    'refine_idx': refine_idx,
                'advanced_config': refine_params_norm,
                    'refine_background': refine_background,
                    'refine_cell': refine_cell,
                    'refine_scale': refine_scale,
                    'refine_instrument': refine_instrument,
                    'refine_absorption': refine_absorption,
                    'refine_atoms': refine_atoms,
                    'use_constraints': use_constraints,
                    'que': id_to_result[id_]
                }
        )

        result_json['state']='created'
        id_to_task[id_].start()
        # id_to_task[id_].kill()
        # id_to_task.pop(id_)

    else:
        if not id_to_task[id_].is_alive():
            info=id_to_result[id_].get()
            id_to_task[id_].join()
            if (not info) or (isinstance(info, dict) and info.get('status') == 'error'):
                error_msg = info.get('error') if isinstance(info, dict) else "中子精修处理失败"
                traceback_msg = info.get('traceback') if isinstance(info, dict) else None

                result_json['state'] = 'completed'
                result_json['status'] = 'error'
                result_json['result'] = {
                    "status": "error",
                    "error": error_msg,
                    "traceback": traceback_msg,
                    "suggestion": "请检查输入文件格式、仪器参数或结构文件是否正确"
                }
                result_json["summary"] = f"GSAS-II中子粉末衍射精修失败: {error_msg}"
                id_to_task[id_] = None
            else:
                # 处理结果
                # print(info)
                id_to_task[id_]=None
                result_json['state']='completed'
                result_json['status']='success'
                result_json["name"] = f"{task_id}_neutron_refinement"
                
                # 计算输出文件大小并构建结果信息
                output_files = []
                total_size_bytes = 0
                total_size_mb = 0
                
                # 检查输出目录中的所有文件
                if os.path.exists(output_dir):
                    for filename in os.listdir(output_dir):
                        file_path = os.path.join(output_dir, filename)
                        if os.path.isfile(file_path):
                            file_size = os.path.getsize(file_path)
                            file_size_mb = file_size / (1024 * 1024)
                            
                            output_files.append({
                                "filename": filename,
                                "path": file_path,
                                "size_bytes": file_size,
                                "size_mb": round(file_size_mb, 3)
                            })
                            
                            total_size_bytes += file_size
                            total_size_mb += file_size_mb
                
                # 构建摘要信息
                rwp = info.get('rwp', 0.0)
                result_json["summary"] = f"GSAS-II中子粉末衍射精修完成，最终Rwp: {rwp:.2f}%\n"

                # 显示启用的精修参数
                enabled_params = []
                if refine_background:
                    enabled_params.append("背景")
                if refine_cell:
                    enabled_params.append("晶胞")
                if refine_scale:
                    enabled_params.append("标度")
                if refine_instrument:
                    enabled_params.append("仪器")
                if refine_absorption:
                    enabled_params.append("吸收")
                if refine_atoms:
                    enabled_params.append("原子")

                result_json["summary"] += f"精修参数: {', '.join(enabled_params) if enabled_params else '无'}\n"
                result_json["summary"] += f"总输出文件大小: {round(total_size_mb, 3)} MB\n"
                result_json["summary"] += f"生成文件数量: {len(output_files)}\n"
                
                # 添加主要文件信息
                if info.get('refined_cif_files'):
                    result_json["summary"] += f"精修CIF文件: {os.path.basename(info['refined_cif_files'][0])}\n"
                
                # 构建详细结果
                result_json['result'] = {
                    "status": "success",
                    "gpx_path": str(Path(output_dir) / f'neutron_refinement_{refine_idx}.gpx'),
                    "lst_path": str(Path(output_dir) / f'neutron_refinement_{refine_idx}.lst'),
                    "plot_path": info.get('plot_path', ''),
                    "refinement_log": info.get('refinement_log', '没有获取到本次精修的日志信息。'),
                    "input_files": {
                        "gsa_file": gsa_path,
                        "instprm_file": instprm_path,
                        "cif_file": cif_paths,
                        "gpx_file": gpx_path
                    },
                    "refinement_config": {
                        "refine_background": refine_background,
                        "refine_cell": refine_cell,
                        "refine_scale": refine_scale,
                        "refine_instrument": refine_instrument,
                        "refine_absorption": refine_absorption,
                        "refine_atoms": refine_atoms,
                        "use_constraints": use_constraints
                    },
                    "output_dir": output_dir,
                    "output_files": output_files,
                    "total_files": len(output_files),
                    "total_size_bytes": total_size_bytes,
                    "total_size_mb": round(total_size_mb, 3),
                    "refinement_results": {
                        "rwp": rwp,
                        "cell_parameters": info.get('cell_params', []),
                        "refined_cif_files": info.get('refined_cif_files', []) or glob(os.path.join(output_dir, '*_refined.cif')),
                        "data_quality": info.get('data_quality', {})
                    },
                    "processing_info": "中子粉末衍射数据精修完成，包含晶胞参数精修、原子位置优化等"
                }

        else:
            result_json['state']='running'
    return result_json



if __name__ == '__main__':
    # 以 SSE 方式启动服务，监听端口 50052
    mcp.run(transport='sse')