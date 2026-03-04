import os
# 导入打包为zip
import shutil

from src.configs.constants import files_dir

def get_run_data(
        run_dir: str,
        save_dir: str = None,
        is_zip: bool = False
) -> dict:
    """
    根据文件夹+run_id获取文件夹所有文件的内容

    Args:
        run_dir: 文件夹前缀
        run_id: run_id
        is_zip: 是否压缩

    Returns:
        {
            "run_id": run_id,
            "files": files,
            "zip_file": zip_file
        }
    """
    if not os.path.exists(run_dir):
        return None
    files = os.listdir(run_dir)

    zip_file = None
    if is_zip:
        save_dir = save_dir or files_dir
        shutil.make_archive(save_dir, 'zip', run_dir)
        zip_file = save_dir+".zip"

    return {
        "files": files,
        "zip_file": zip_file
    }
    

def get_groups_files(
        files: list[str],
        group_ids: list[str]
) -> dict:
    """
    根据group_ids获取文件列表

    Args:
        files: 文件列表
        group_ids: group_ids

    Returns:
        {
            "group_id": group_id,
            "files": group_files
        }
    """
   
    