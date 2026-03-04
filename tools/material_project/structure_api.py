
from emmet.core.summary import SummaryDoc
from pymatgen.io.cif import CifWriter
from mp_api.client import MPRester
import os
import dotenv
dotenv.load_dotenv()
mp_api_key = os.environ.get("MP_API_KEY")

def mp_search_cif_api(
        cif_file_dir: str,
        mp_ids: list[str],
        # thread_id: str,
) -> dict:
    try:
        cif_files = {}
        with MPRester(mp_api_key) as mpr:
            docs: list[SummaryDoc] | list[dict] = mpr.materials.summary.search(
                material_ids=mp_ids
            )
            for doc in docs:
                structure = doc.structure
                cif_writer = CifWriter(structure)
                cif_path = f"{cif_file_dir}/{doc.material_id.__str__()}.cif"
                cif_writer.write_file(cif_path)
                cif_files.update({doc.material_id.__str__(): cif_path})
        return cif_files
    except Exception as e:
        print(f"Error in mp_search_cif_api: {e}")
        return {}

if __name__ == "__main__":
    mp_ids = ["mp-149"]
    run_dir = "/aifs/user/home/xiongdb/VSproject/rozai_agent/test_datesets/RUN0021905/"
    thread_id = "test_thread"
    cif_files = mp_search_cif_api(run_dir, mp_ids, thread_id)
    print(cif_files)
