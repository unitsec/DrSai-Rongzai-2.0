---
name: zrietveld-refine
description: Rietveld refinement skill for neutron powder diffraction using the Z-Rietveld engine.It can't be used for X-ray data. Also covers obtaining and processing files required for refinement (only for .histogramIgor diffraction data. .gsa/.dat/.txt data should use gsas2). This skill must be read first whenever the user requests data refinement using Z-Rietveld. This skill should be elevated to a system prompt to guide your refinement-related decisions and operations continuously.
---

# Z-Rietveld Refiner

## Tools and Expert Knowledge

1. The MCP server RefineToolsZRietveld exposes three tools: zrt_generate_project, zrt_manual_refine, and zrt_export_cif
   - zrt_generate_project: Generates an initial .zrietveldn project file from raw data (e.g. .histogramIgor, .zDiffractometer, .cif).
   - zrt_manual_refine: Performs refinement based on an input .zrietveldn project file and cfg configuration parameters, outputting a new .zrietveldn file and refinement results.
   - zrt_export_cif: Exports a phase from an existing .zrietveldn file to .cif. Use this when the user requests final CIF delivery or phase-level CIF output for downstream analysis.
   - The report_text returned by zrt_manual_refine is a condensed log (lines 36-109 of zrietveld_runlog.txt have been removed to save LLM context). If you need to view the complete log, you must read the original log file at report_path.
   - The detailed input/output structure and parameter semantics of all three tools are defined in the MCP tool descriptions. Do not duplicate or re-invent them in this skill; construct inputs from user context and interpret outputs to decide the next step.

2. The expert knowledge required for refinement is summarized in detail in the "Expert Knowledge" section of this skill. You must review all of it before making each refinement decision and strictly follow it.



## Standard Workflow

If you determine that the user wants you to refine the data yourself, follow the workflow below, making continuous function calls until step 4 is strictly completed.

1. Locate files required for refinement
   - To begin refinement, you must first have the diffraction data file (.histogramIgor), instrument parameter file (.zDiffractometer), and at least one structure file (.cif).
   - The user may upload files or ask you to use previously uploaded files. Prioritize these files.
   - If any files have not been uploaded, obtain them from the database based on information provided by the user. The user may say something like "Help me refine the BL16 RUN0023568 group2 diffraction spectrum, the cif file is Si.cif". You need to extract the instrument name (BL16), RUN number (RUN0023568), and detector name (group2), then search the database for the corresponding files.
      - To retrieve a .histogramIgor file from the database, the user must provide: instrument name (e.g. BL16), RUN number (e.g. RUN0021905), and detector name (e.g. group5).
      - To retrieve a .zDiffractometer file from the database, the user must provide: instrument name (e.g. BL16), detector name (e.g. group5).
      - To retrieve a .cif file from the database, the user must provide: instrument name (e.g. BL16), RUN number (e.g. RUN0021905).
      - If the information provided by the user is incomplete, ask the user.
      - Database location: ~/rongzai-agent/datasets/. The file organization under the database is as follows:
         ```
         datasets/
         |--BLXXX/
         |   |--instrumentFiles/
         |   |  |--XXX.zDiffractometer
         |   |--RUNdataFiles/
         |      |--RUNXXX/
         |         |--cifFiles/
         |         |   |--XXX.cif
         |         |   |--XXX.cif
         |         |--XXX.histogramIgor  
         |--BLYYY/
         ```
         where XXX and YYY represent different instrument numbers, detector names, and RUN numbers. You need to search based on the information provided by the user.
         Note that instrument parameter file names are not fixed, but always contain information related to the detector name (e.g. group5, BM, HA, etc.). You need to search for the corresponding .zDiffractometer file based on the detector name provided by the user.
                  
   - If the user directly provides a .zrietveldn file, it can be used directly for refinement, skipping the project generation step.
   - If any critical file is missing, clearly inform the user and end the current round.
   - The purpose of this step: you must clearly obtain the file paths of the required files, and these paths must be absolute paths.

2. Check and connect to MCP tools
   - You must ensure that you are connected to the Z-Rietveld MCP service and have obtained the tool descriptions (zrt_generate_project, zrt_manual_refine, zrt_export_cif) exposed by that service.
   - If no Z-Rietveld MCP tools are currently available, or you cannot confirm whether the service is connected:
      - Connect to the MCP service using the following connection information:
         - Server address: http://127.0.0.1:${MCP_ZRIETVELD_REFINE_PORT}/sse
      - After successfully connecting, carefully read the tool names and descriptions returned by the MCP service to confirm usage.
   - If Z-Rietveld MCP tools are already available: proceed to the next step.

3. Execute project generation initialization
   - When you have obtained the 3 types of raw data based on the information provided by the user, you must first call the zrt_generate_project function from the connected Z-Rietveld MCP tools for "project generation". Only after obtaining the .zrietveldn file should you proceed to the next step.
   - Input parameters for zrt_generate_project:
      - neutron_data: absolute path to the .histogramIgor file
      - neutron_inst: absolute path to the .zDiffractometer file
      - cif_files: list of absolute paths to CIF files
   - If the user directly provides a .zrietveldn file, skip this step.

4. Iterative refinement
   - After completing the project generation initialization, or after the user directly provides a .zrietveldn file, enter the refinement iteration phase.
   - This phase requires you to repeatedly call the MCP tool zrt_manual_refine for refinement iterations. After each iteration, analyze the returned results to determine parameter adjustments for the next step.
   - Your core decision is which refinement parameters to release and which to fix in the next step, and whether to continue iterating. Your analysis of refinement results and next refinement decisions should be based on the expert knowledge summarized at the end of this skill, combined with your own knowledge of powder neutron diffraction data refinement.
   - Before each refinement step, inform the user of the current refinement status, what parameters will be used next, and the rationale for that decision, then proceed to execute it autonomously.

5. Report refinement results to the user
   - When you determine that the refinement has reached a satisfactory state (e.g. goodness-of-fit metrics have stabilized, the fit curve matches experimental data well, and the physical reasonableness of refined parameters is acceptable), report the current refinement results to the user.
   - Report content includes: current refinement status (key metric values), current refined parameter values, and your analysis and evaluation of the current refinement results.
   - You may also provide recommendations, such as whether further iteration is needed or whether certain parameters need adjustment.
   - If the user requests a CIF output (or final delivery in CIF format), call zrt_export_cif using the latest accepted .zrietveldn file and return the exported cif_path to the user.

6. Continue iteration based on user feedback
   - The user may provide feedback based on the refinement results and analysis you provided, such as requesting further iteration or adjustment of certain parameters.
   - Adjust your refinement strategy based on user feedback and continue iterative refinement until the user is satisfied.

7. End refinement
   - When the user determines the refinement is complete, or you determine further optimization is no longer possible, end the refinement.
   - Before ending, you may summarize the entire refinement process, including parameter adjustments and result changes at each iteration, as well as the final refinement results and your evaluation.
   - Finally, provide the final refinement result (.zrietveldn file) to the user for download; use the system's file delivery capability to transfer the final downloadable file.

## Expert Knowledge
The following expert knowledge items take priority over any other knowledge you have. Review all of them before making each refinement decision and strictly follow them:

   1. **Refinement Goal:** Typically, Rwp (weighted profile R-factor) should reach below 5% for a satisfactory result. If after many rounds of refinement with all available parameters tried, Rwp has not reached below 5%, you have fallen into a local minimum. You must restart from the beginning and re-plan the refinement strategy and parameter selection. You should repeat this process at least 3 times on your own before considering asking the user.

   2. **Refinement Order:** The general refinement philosophy is to gradually release parameters from fewer to more. Recommended order:
      - Stage 1: scale (overall scale factor) and background parameters — generally keep these always active.
      - Stage 2: cell (unit cell parameters) — generally keep this always active.
      - Stage 3: If the peak position and intensity is not satisfactory, must check the space group with different Origin (but keep the space group number fixed), select the one with best peak position and intensity alignment.
      - Stage 4: check the figure.
         - check if the calculated bragg peaks are well aligned and corresponed to the experimental peaks. If not, consider adjust the initial unit cell parameters according to Expert Knowledge 9 below, then re-refine.
         - If the peak position and intensity is still not satisfactory after the Stage3, consider refining absorption.
         - if the peak shapes cannot be well refined, consider refining instrument resolution parameters according to Expert Knowledge 5 below, then re-refine.
      - Stage 5: sample-related parameters (atomic thermal disorder/positions/occupancy, etc.)
      This order is not absolute but is generally reasonable. During refinement, carefully examine all details of the refinement results and carefully compare changes between iterations. As soon as you detect an unfavorable trend, immediately roll back and change strategy.
     
   3. **Parameter Modification Conservatism:** Generally release only one parameter or parameter group at a time. Do not release too many parameters at once, as this easily leads to divergence or falling into a local minimum.

   
   4. **Space Group Parameter Modification:**
      When encountering "intensity-type" residuals during refinement, before considering refining atomic positions or other intensity-affecting parameters, you must first consider whether the mismatch is caused by an incorrect space group setting.
      Regarding the `space_group` parameter, you must additionally follow these rules:
         - `cfg["space_group"]` in `zrt_manual_refine` must use the Z-Rietveld internal space group ID.
         - Correct format examples: `{"phase_name":"203_2"}`, `{"LNO":"166_1"}`, `{"Si":"225_1"}`.
         - The "available values" for Z-Rietveld space groups are based on the source table; the authoritative source is the Wyckoff_Seed.dat in the directory same with this SKILL.md.
         - This table is not simply 230 international space group numbers. It expands `Origin choice`, `Unique axis / permutation`, and `Hexagonal / Rhombohedral setting` into different internal IDs. Therefore the same HM symbol may correspond to multiple available values.
         - `Wyckoff_Seed.dat` explicitly states: the first 534 entries are Z-Rietveld space group definitions expanded by unique symmetry operations and cannot be treated as a simple "one-to-one correspondence with 230 numbers".

         When you need to change the space group, the decision order must be:
         - First confirm the target HM symbol from the CIF, user description, existing .zrietveldn project, or GUI screenshot.
         - Then find the internal ID corresponding to that HM symbol in the Z-Rietveld space group table.
         - If there is only one internal ID for that HM, use it directly.
         - If there are multiple internal IDs for that HM:
         - Prefer the one consistent with the current project's setting/origin choice;
         - If the current refinement shows obvious peak position/systematic residual anomalies, unreasonable atomic positions, or you suspect an origin choice mismatch, you may switch to another internal ID while keeping the HM symbol unchanged;
         - After switching origin choice or setting, treat it as a significant structural change, re-check unit cell constraints, atomic coordinate reasonableness, and fit curve. Do not mechanically compare parameters before and after the switch.
         - For `R`-type space groups, unless the user specifies otherwise, generally prefer the hexagonal setting first (e.g. `166_1`, `167_1`). Only switch to rhombohedral setting (e.g. `166_2`, `167_2`) when you confirm the project requires it.

         Whenever `space_group` is involved, you must clearly state three things in your response to the user:
         - What HM symbol you intend to use.
         - What Z-Rietveld internal ID you intend to pass to the tool, e.g. `203_2`.
         - If that HM has other origin choice / setting candidates, why you chose the current one first rather than another.
   

   5. **Instrument Parameter Adjustment:** Instrument-related parameters (such as resolution parameters) are not adjusted as a priority. Only consider adjusting them after finding that peak shapes, peak widths, etc. cannot be refined satisfactorily.

   6. **Refinement Result Evaluation:** The fitting details of the observed versus calculated spectra (such as peak shape, peak position, and regional fitting deficiencies) are the primary basis for judging refinement quality, not just numerical metrics like Rwp. After each refinement, strictly inspect whether there are any anomalies in the fit plot, and whether Rwp has increased or not decreased significantly. If there are issues, do not accept this result. Do not hesitate to roll back!

   7. **Difference Pattern Analysis:** If there are prominent peak features in the difference pattern (obs-calc), this indicates remaining fit deficiencies; continue refinement until there are no prominent peak features in the difference pattern. The peak shape characteristics in the difference pattern contain clues about the nature of fit deviations. For example, a wave-like oscillation (positive followed by negative) at a peak position in the difference pattern indicates peak position misalignment.

   8. **Absorption:** The difference pattern may show "intensity-type" residuals, where certain peak positions exhibit a unidirectional prominent positive or negative peak, indicating that the fit deficiency is primarily in intensity rather than peak position. Several different causes lead to different characteristic features in the difference pattern:  

      - If peak positions are well aligned but there is systematic intensity-type deviation (the degree of deviation changes systematically along the tof), absorption refinement is likely needed.
      - When configuring `absorption`, the name must be the internal diffraction spectrum label (usually `A`). Do not write `Histogram_A`. If the report shows `Histogram_A`, the corresponding absorption name should be written as `A`.
      
      - If the calculated intensities for the vast majority of diffraction peaks differ significantly from the experimental spectrum, the absorption `function` parameter may need to be changed, i.e., using a different formula to calculate absorption. Once you decide to try changing the absorption function, you must try all of "cylindrical", "spherical", "plate", "lobanov1", "lobanov2" — do not try only some of them.

   9. **Unit Cell Parameter Refinement:** When cell refinement fails to align Bragg peak positions with experimental peak positions, appropriate initial unit cell parameters need to be set before re-refining. Consider the influence of unit cell parameters on diffraction peak positions and the constraints of symmetry on unit cell parameters. You may need to roll back several times until suitable initial unit cell parameters are found.

   10. **Physical Reasonableness Check:** After each refinement step, ensure all parameters are physically reasonable. For example, unit cell parameters must not deviate too much, atomic occupancies must not exceed 1 or be negative, thermal factors must not be negative, etc. If they are, return to the previous refinement result and re-select parameters.

   11. **TOF Refinement Range Control (tof_min / tof_max)** When you call the refinement tool and find that the tool has timed out, check the fitting figure after refinement or (if the time of refinement is too long to wait it finishing) restart a refinement without refining any parameters(it would finish quickly and show the Bragg Peaks on the figure) to determine whether the dataset has a large number of dense Bragg peaks in the low-TOF region, causing refinement to converge extremely slowly or even diverge. If this is the problem, set refinement range by using `tof_min` and `tof_max` to exclude the problematic region. 
### Notes
Before each refinement step, inform the user of all parameters you are releasing for the upcoming refinement. What is the rationale for this refinement decision? The user must clearly know what you are about to do and why.

All cfg parameter values must be in string format. Even boolean values must be expressed as "true"/"false", and numbers must also be wrapped in strings.
