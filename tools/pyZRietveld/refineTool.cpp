#include <iostream>
#include <fstream>
#include <chrono>
#include <iomanip>
#include <sstream>
#include <set>
#include <map>
#include <algorithm>
#include <limits>
#include <filesystem>
#include "Z-Rietveld/Z-Rietveld/nlohmann/json.hpp"
#include "Z-Rietveld/Z-Rietveld/Z-Rietveld.h"
#include <stdexcept>

#if defined(BUILD_PYBIND)
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#endif

using namespace std;

#if defined(BUILD_PYBIND)
namespace py = pybind11;
#endif

std::map<std::string,std::string> runRefine(shared_ptr<ZRTRietveld> rietveld, shared_ptr<ZRTFilePath> outPathObj, bool singleRun = false);

// Configure a manual refinement: set which parameters to vary and initialize values.
// New: configure manual refinement from a configuration dictionary
// Supports both legacy parameter names and new standardized names
std::map<std::string,std::string> ManualRefinementWithDict(const std::string& ZRTnPathStr, const std::map<std::string,std::string>& cfg)
{

    // read the generated zrietveldn
    shared_ptr<ZRTError> error = nullptr;
    std::shared_ptr<ZRTFilePath> outPathObj = ZRTFilePath::CreateWithUTF8String(ZRTnPathStr);
    shared_ptr<ZRTDocument> zrtnFile = ZRTDocument::CreateWithReadCborFile(outPathObj, &error);
    if (ZRTError::HasError(&error) || !zrtnFile) {
        std::map<std::string,std::string> ret;
        ret["ok"] = "false";
        ret["fit_status"] = "load_error";
        ret["error"] = error ? *error->GetAllMessage() : "Failed to open zrietveldn file.";
        if (error) cout << *error->GetAllMessage() << endl;
        return ret;
    }

    // Create and configure the Rietveld object from the read document
    shared_ptr<ZRTRietveld> rietveld = zrtnFile->GetCurrentRietveld();
    rietveld->SetHistoryIndex(zrtnFile->GetCurrentHistoryIndex());
    shared_ptr<ZRTPreference> preference = ZRTPreference::CreateForDefault();
    preference->SetNeedLocalLog(false);
    rietveld->SetPreference(preference);
    shared_ptr<ZRTRunManager> runManager = ZRTRunManager::CreateForDefault();
    rietveld->SetRunManager(runManager);
    rietveld->SetDocument(zrtnFile.get());

    // Helper: get value from config with fallback to default, supporting parameter aliases
    auto getOrDefault = [&](const std::string& k, const std::string& d)->std::string{
        auto it = cfg.find(k); return (it==cfg.end())? d : it->second;
    };

    // Helper: get value with alias support (tries primary key first, then alias)
    auto getWithAlias = [&](const std::string& primary, const std::string& alias, const std::string& def)->std::string{
        auto it = cfg.find(primary);
        if (it != cfg.end()) return it->second;
        it = cfg.find(alias);
        if (it != cfg.end()) return it->second;
        return def;
    };

    auto toInt = [&](const std::string& s, int def)->int{
        try{ return std::stoi(s); }catch(...){ return def; }
    };
    auto toDouble = [&](const std::string& s, double def)->double{
        try{ return std::stod(s); }catch(...){ return def; }
    };
    auto toBool = [&](const std::string& s, bool def)->bool{
        std::string t = s; for (auto &c : t) c = (char)tolower(c);
        if (t=="1"||t=="true"||t=="yes") return true;
        if (t=="0"||t=="false"||t=="no") return false;
        return def;
    };
    auto trimCopy = [&](const std::string& in)->std::string{
        std::string s = in;
        auto b = s.find_first_not_of(" \t\n\r");
        if (b == std::string::npos) return "";
        auto e = s.find_last_not_of(" \t\n\r");
        return s.substr(b, e - b + 1);
    };
    auto toLowerCopy = [&](const std::string& in)->std::string{
        std::string s = in;
        std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c){ return (char)std::tolower(c); });
        return s;
    };
    auto replaceAll = [&](std::string s, const std::string& from, const std::string& to)->std::string{
        if (from.empty()) return s;
        size_t start = 0;
        while ((start = s.find(from, start)) != std::string::npos) {
            s.replace(start, from.length(), to);
            start += to.length();
        }
        return s;
    };
    auto absorptionTypeFromName = [&](const std::string& name)->ZRTIntensityCorrectionAbsorptionType{
        std::string t = toLowerCopy(trimCopy(name));
        if (t.empty() || t == "none") return ZRTIntensityCorrectionAbsorptionTypeNone;
        if (t == "cylindrical" || t == "cylinder" || t == "cyl") return ZRTIntensityCorrectionAbsorptionTypeCylindrical;
        if (t == "spherical" || t == "sphere") return ZRTIntensityCorrectionAbsorptionTypeSpherical;
        if (t == "plate") return ZRTIntensityCorrectionAbsorptionTypePlate;
        if (t == "flatplate" || t == "flat_plate") return ZRTIntensityCorrectionAbsorptionTypeFlatPlate;
        if (t == "lobanov1" || t == "lobanov_smaller_than_3" || t == "lobanov<3" || t == "lobanov_small") return ZRTIntensityCorrectionAbsorptionTypeLobanovSmallerThan3;
        if (t == "lobanov2" || t == "lobanov_bigger_than_3" || t == "lobanov>3" || t == "lobanov_big") return ZRTIntensityCorrectionAbsorptionTypeLobanovBiggerThan3;
        return ZRTIntensityCorrectionAbsorptionTypeUnknown;
    };

    // disable autoAnalyze
    try { if (rietveld->GetAutoAnalyze()) rietveld->GetAutoAnalyze()->SetUse(false); } catch(...) {}

    // Keep convergence condition from the input project in manual refine.
    
    // === SPACE GROUP SETTINGS ===
    // cfg key: "space_group", value: dict string e.g. "{'LNO':'166_1','phase2':'225_1'}"
    // Keys are phase labels; values are Z-Rietveld internal strings.
    // If the key is absent, space groups are left unchanged.
    auto sgDictIt = cfg.find("space_group");
    if (sgDictIt != cfg.end() && !sgDictIt->second.empty()) {
        std::cerr << "[space_group][debug] raw cfg value: " << sgDictIt->second << std::endl;
        // Parse dict-like string into label -> sgStr map
        std::map<std::string, std::string> sgMap;
        std::string dictStr = sgDictIt->second;
        for (auto& c : dictStr) if (c == '{' || c == '}' || c == '\'' || c == '"') c = ' ';
        std::istringstream sgSS(dictStr);
        std::string sgTok;
        while (std::getline(sgSS, sgTok, ',')) {
            auto col = sgTok.find(':');
            if (col == std::string::npos) continue;
            std::string lbl = sgTok.substr(0, col);
            std::string val = sgTok.substr(col + 1);
            lbl.erase(0, lbl.find_first_not_of(" \t")); lbl.erase(lbl.find_last_not_of(" \t") + 1);
            val.erase(0, val.find_first_not_of(" \t")); val.erase(val.find_last_not_of(" \t") + 1);
            if (!lbl.empty() && !val.empty()) sgMap[lbl] = val;
        }
        std::cerr << "[space_group][debug] parsed mappings: " << sgMap.size() << std::endl;
        for (const auto& kv : sgMap) {
            std::cerr << "[space_group][debug]   key='" << kv.first << "' -> '" << kv.second << "'" << std::endl;
        }
        try {
            auto cm = rietveld->GetCrystalModel();
            if (cm && cm->GetPhases()) {
                std::set<std::string> matchedKeys;
                for (auto& phase : *cm->GetPhases()) {
                    if (!phase) continue;
                    const std::string phaseLabel = phase->GetLabelForAnalyze();
                    std::cerr << "[space_group][debug] phase label in project: '" << phaseLabel << "'" << std::endl;

                    auto it = sgMap.find(phaseLabel);
                    if (it == sgMap.end()) {
                        std::cerr << "[space_group][debug]   no mapping found, skip" << std::endl;
                        continue;
                    }
                    matchedKeys.insert(it->first);

                    auto newSg = make_shared<ZRTSpaceGroup>();
                    newSg->SetZRietveldSpaceGroup(it->second);
                    if (newSg->GetSpaceGroupNumber() != dummy_sg) {
                        phase->SetSpaceGroup(newSg);
                        std::cerr << "[space_group][debug]   applied: '" << phaseLabel
                                  << "' -> '" << it->second << "'" << std::endl;
                    } else {
                        std::cerr << "WARNING: Unknown space group string '" << it->second
                                  << "' for phase '" << phase->GetLabelForAnalyze()
                                  << "'. Keeping original." << std::endl;
                    }
                }
                for (const auto& kv : sgMap) {
                    if (0 == matchedKeys.count(kv.first)) {
                        std::cerr << "[space_group][debug] unmapped key in cfg: '" << kv.first << "'" << std::endl;
                    }
                }
            }
        } catch(...) {}
    }

    // === BACKGROUND SETTINGS ===
        // Support both legacy (bgOrder) and new (background_coeffs) parameter names
    bool refineBackground = toBool(getWithAlias("refine_background", "varyBackground", "false"), false);
    int bgOrder = toInt(getWithAlias("background_coeffs", "bgOrder", "6"), 6);
    std::string bgInitStr = getOrDefault("bgInit", "");
    std::vector<double> initVals;
    if (!bgInitStr.empty()) {
        std::istringstream ss(bgInitStr);
        std::string tok;
        while (std::getline(ss, tok, ',')){
            try{ initVals.push_back(std::stod(tok)); } catch(...){ initVals.push_back(0.0); }
        }
    }
    if (initVals.size() < (size_t)bgOrder) initVals.resize(bgOrder, 0.0);
    if (initVals.size() > 0) initVals[0] = initVals[0]; else if (bgOrder>0) initVals[0]=1.0;

    std::string bgMode = getOrDefault("bgMode", "legendre");

        // apply histogram-level background settings
    try {
        auto hists = rietveld->GetHistograms();
        if (hists) {
            for (auto &h : *hists) {
                if (!h) continue;
                try {
                    auto dif = h->GetDiffractometer();
                    if (!dif) continue;

                    if (bgMode == "sonneveld") {
                        auto s = ZRTBackgroundSonneveld::CreateForDefault();
                        // ensure sample points / coefficients according to bgOrder
                        while ((int)s->GetLegendreCoefficientEstimateds()->size() < bgOrder) s->AppendLegendreCoefficientEstimated();
                        auto coeffs = s->GetLegendreCoefficientEstimateds();
                        for (int i=0;i<bgOrder && i<(int)coeffs->size();++i){ auto c = coeffs->at(i); if(!c) continue; try{ c->SetValue(initVals[i]); c->SetID(ZRTIDVary); }catch(...){} }
                        auto bg = make_shared<ZRTBackground>(); bg->SetMode(ZRTBackgroundModeLegendreEstimated); bg->SetSonneveld(s); dif->SetBackground(bg);
                    } else {
                        auto bg = make_shared<ZRTBackground>();
                        bg->SetMode(ZRTBackgroundModeLegendre);
                        auto leg = ZRTBackgroundLegendre::CreateForDefault();
                        while ((int)leg->GetCoefficients()->size() < bgOrder) leg->AppendCoefficient();
                        auto coefs = leg->GetCoefficients();
                        for (int i = 0; i < bgOrder && i < (int)coefs->size(); ++i) {
                            auto c = coefs->at(i);
                            if (!c) continue;
                            try { c->SetValue((i < (int)initVals.size()) ? initVals[i] : 0.0); } catch(...) {}
                            // Set ID based on refineBackground flag
                            try { c->SetID(refineBackground ? ZRTIDVary : ZRTIDFixed); } catch(...) {}
                            try { c->SetSelectMode(ZRTValueIDSelectModeVaryFixed); } catch(...) {}
                        }
                        bg->SetLegendre(leg);
                        dif->SetBackground(bg);
                    }
                } catch(...){}
            }
        }
    } catch(...){}


    // === SCALE SETTINGS ===
        // Support both legacy (scaleVary, scaleInit) and new (refine_scale) parameter names
    bool hasScaleInit = (cfg.find("scaleInit") != cfg.end());
    double scaleInit = toDouble(getOrDefault("scaleInit","1.0"), 1.0);
    bool scaleVary = toBool(getWithAlias("refine_scale", "scaleVary", "false"), false);
    try {
        auto hists = rietveld->GetHistograms();
        if (hists) {
            for (auto &h : *hists) {
                if (!h) continue;
                try {
                    auto sw = h->GetScaleWhole();
                    if (sw) {
                        auto sval = sw->GetScale();
                        if (sval) {
                            // Only overwrite initial value if provided in the config dictionary.
                            if (hasScaleInit) try { sval->SetValue(scaleInit); } catch(...) {}
                            try { sval->SetID(scaleVary ? ZRTIDVary : ZRTIDFixed); } catch(...) {}
                            try { sval->SetSelectMode(ZRTValueIDSelectModeVaryFixed); } catch(...) {}
                        }
                    }
                } catch(...) {}
            }
        }
    } catch(...) {}

    // === === PROFILE FUNCTION (PEAK SHAPE) REFINEMENT ===
    // Accept a single "refine_profile" key whose value is a comma/space-separated list of tokens:
    //   level0 / level1 / level2  -> SetPresetIDLevel(0/1/2)  (mutually exclusive; level0 takes priority)
    //   alpha                     -> refine alpha parameters
    //   beta0                     -> refine beta0 parameters
    //   beta1                     -> refine beta1 parameters
    //   r0                        -> refine r0 parameters
    // Example: "refine_profile" = "level1,alpha,beta0"
    // Legacy per-key names (refine_profile_level0, etc.) are still accepted as fallback.
    auto parseProfileTokens = [](const std::string& s) -> std::set<std::string> {
        std::set<std::string> tokens;
        std::string cur;
        for (char c : s) {
            if (c == ',' || c == ' ' || c == ';') {
                if (!cur.empty()) { tokens.insert(cur); cur.clear(); }
            } else {
                cur += (char)std::tolower((unsigned char)c);
            }
        }
        if (!cur.empty()) tokens.insert(cur);
        return tokens;
    };
    std::string refineProfileRaw = trimCopy(getOrDefault("refine_profile", ""));
    std::set<std::string> profileTokens = parseProfileTokens(refineProfileRaw);
    // Fallback: also honour legacy per-key flags so old callers keep working.
    bool refineProfileLevel0 = profileTokens.count("level0") > 0 || toBool(getOrDefault("refine_profile_level0", "false"), false);
    bool refineProfileLevel1 = profileTokens.count("level1") > 0 || toBool(getOrDefault("refine_profile_level1", "false"), false);
    bool refineProfileLevel2 = profileTokens.count("level2") > 0 || toBool(getOrDefault("refine_profile_level2", "false"), false);
    bool refineAlpha = profileTokens.count("alpha") > 0 || toBool(getOrDefault("refine_profile_alpha", "false"), false);
    bool refineBeta0 = profileTokens.count("beta0") > 0 || toBool(getOrDefault("refine_profile_beta0", "false"), false);
    bool refineBeta1 = profileTokens.count("beta1") > 0 || toBool(getOrDefault("refine_profile_beta1", "false"), false);
    bool refineR0    = profileTokens.count("r0")    > 0 || toBool(getOrDefault("refine_profile_r0",    "false"), false);

    auto hists = rietveld->GetHistograms();
    if (hists) {
        for (auto& h : *hists) {
            if (!h) continue;
            auto profileFunctions = h->GetProfileFunctions();
            if (profileFunctions) {
                for (auto& pf : *profileFunctions) {
                    if (!pf) continue;
                    // Helper: set a ZRTGlobalItem to Vary (SetPhase + SetForceID)
                    // ZRTGlobalItem::SetID() is disabled (abort); must use SetPhase + SetForceID.
                    ZRTPhase* pfPhase = pf->GetPhase();
                    auto varyItem = [&](shared_ptr<ZRTGlobalItem> item) {
                        if (!item) return;
                        item->SetPhase(pfPhase);
                        item->SetForceID(ZRTIDVary);
                    };

                    // TOF0 profile function
                    if (pf->GetType() == ZRTProfileFunctionTypeTOF0) {
                        auto pfTOF0 = dynamic_pointer_cast<ZRTProfileFunctionTOF0>(pf);
                        if (pfTOF0) {
                            if (refineProfileLevel0)      pfTOF0->SetPresetIDLevel(0);
                            else if (refineProfileLevel1) pfTOF0->SetPresetIDLevel(1);
                            else if (refineProfileLevel2) pfTOF0->SetPresetIDLevel(2);
                            if (refineAlpha) {
                                varyItem(pfTOF0->GetAlpha()->GetAlpha0());
                            }
                            if (refineBeta0) {
                                varyItem(pfTOF0->GetBeta0()->GetBeta00());
                                varyItem(pfTOF0->GetBeta0()->GetBeta01());
                            }
                            if (refineBeta1) {
                                varyItem(pfTOF0->GetBeta1()->GetBeta10());
                            }
                            if (refineR0) {
                                varyItem(pfTOF0->GetR0()->GetR0());
                            }
                        }
                    }
                    // TOF0m profile function (e.g. BM instrument)
                    // Alpha: Alpha1 + Alpha2; R0: R01/R02/R03 (different from TOF0)
                    else if (pf->GetType() == ZRTProfileFunctionTypeTOF0m) {
                        auto pfTOF0m = dynamic_pointer_cast<ZRTProfileFunctionTOF0m>(pf);
                        if (pfTOF0m) {
                            if (refineProfileLevel0)      pfTOF0m->SetPresetIDLevel(0);
                            else if (refineProfileLevel1) pfTOF0m->SetPresetIDLevel(1);
                            else if (refineProfileLevel2) pfTOF0m->SetPresetIDLevel(2);
                            if (refineAlpha) {
                                varyItem(pfTOF0m->GetAlpha()->GetAlpha1());
                                varyItem(pfTOF0m->GetAlpha()->GetAlpha2());
                            }
                            if (refineBeta0) {
                                varyItem(pfTOF0m->GetBeta0()->GetBeta00());
                                varyItem(pfTOF0m->GetBeta0()->GetBeta01());
                            }
                            if (refineBeta1) {
                                varyItem(pfTOF0m->GetBeta1()->GetBeta10());
                            }
                            if (refineR0) {
                                varyItem(pfTOF0m->GetR0()->GetR01());
                                varyItem(pfTOF0m->GetR0()->GetR02());
                                varyItem(pfTOF0m->GetR0()->GetR03());
                            }
                        }
                    }
                }
            }
        }
    }
    
    // === CRYSTAL MODEL OPTIONS ===
        // Support both legacy and new parameter names
    bool varyMassRatio = toBool(getWithAlias("refine_phase_fraction", "varyMassRatio", "false"), false);
    bool varyLattice = toBool(getWithAlias("refine_cell", "varyLattice", "false"), false);
    bool varyOccupancy = toBool(getWithAlias("refine_occupancy", "varyOccupancy", "false"), false);
    double adpThreshold = toDouble(getOrDefault("adpVaryThreshold","0.0"), 0.0);

        // Atomic position refinement
    bool varyPosition = toBool(getOrDefault("refine_position", "false"), false);

    // Size and microstrain refinement
    bool refineSize = toBool(getOrDefault("refine_size", "false"), false);
    bool refineMustrain = toBool(getOrDefault("refine_mustrain", "false"), false);

    // Absorption refinement
    bool refineAbsorption = toBool(getOrDefault("refine_absorption", "false"), false);
    // Optional per-histogram absorption config (dict-like string), e.g.
    // {'A': {'refine': True, 'function': 'spherical'}, '_default': {'function': 'cylindrical'}}
    nlohmann::json absorptionCfg;
    bool hasAbsorptionCfg = false;
    {
        std::string absorptionCfgRaw = trimCopy(getOrDefault("absorption", ""));
        if (!absorptionCfgRaw.empty()) {
            std::string normalized = absorptionCfgRaw;
            std::replace(normalized.begin(), normalized.end(), '\'', '"');
            normalized = replaceAll(normalized, " True", " true");
            normalized = replaceAll(normalized, ":True", ":true");
            normalized = replaceAll(normalized, " False", " false");
            normalized = replaceAll(normalized, ":False", ":false");
            normalized = replaceAll(normalized, " None", " null");
            normalized = replaceAll(normalized, ":None", ":null");
            try {
                absorptionCfg = nlohmann::json::parse(normalized);
                hasAbsorptionCfg = absorptionCfg.is_object();
                if (!hasAbsorptionCfg) {
                    std::cerr << "WARNING: cfg['absorption'] is not a dict/object. Ignored." << std::endl;
                }
            } catch (const std::exception& e) {
                std::cerr << "WARNING: failed to parse cfg['absorption']: " << e.what() << std::endl;
            }
        }
    }

    // Instrument parameter refinement flags
    bool refineInstrumentAlpha = toBool(getOrDefault("refine_instrument_alpha", "false"), false);
    bool refineInstrumentBeta = toBool(getOrDefault("refine_instrument_beta", "false"), false);
    bool refineInstrumentSig = toBool(getOrDefault("refine_instrument_sig", "false"), false);
    bool refineInstrumentX = toBool(getOrDefault("refine_instrument_x", "false"), false);
    bool refineInstrumentY = toBool(getOrDefault("refine_instrument_y", "false"), false);
    bool refineInstrumentZ = toBool(getOrDefault("refine_instrument_z", "false"), false);

    // Parse refine_atoms dict: {'Fe1':'XU', 'O2':'YZF', 'all':'XU'}
    // Legacy: X means XYZ all; also supports explicit Y/Z.
    // U = Uiso (AtomicDisplacement12), F = occupancy
    std::map<std::string, std::string> refineAtomsMap;
    auto refineAtomsIt = cfg.find("refine_atoms");
    if (refineAtomsIt != cfg.end() && !refineAtomsIt->second.empty()) {
        // Simple parser for dict-like string: "{'Fe1':'XU','O2':'XUF'}" or "{'all':'XU'}"
        std::string atomsStr = refineAtomsIt->second;
        // Remove braces and quotes
        for (auto& c : atomsStr) if (c == '{' || c == '}' || c == '\'' || c == '"') c = ' ';
        std::istringstream ss(atomsStr);
        std::string token;
        while (std::getline(ss, token, ',')) {
            auto colonPos = token.find(':');
            if (colonPos != std::string::npos) {
                std::string atomLabel = token.substr(0, colonPos);
                std::string flags = token.substr(colonPos + 1);
                // Trim whitespace
                atomLabel.erase(0, atomLabel.find_first_not_of(" \t"));
                atomLabel.erase(atomLabel.find_last_not_of(" \t") + 1);
                flags.erase(0, flags.find_first_not_of(" \t"));
                flags.erase(flags.find_last_not_of(" \t") + 1);
                if (!atomLabel.empty() && !flags.empty()) {
                    refineAtomsMap[atomLabel] = flags;
                }
            }
        }
    }

    

    // === INSTRUMENT PARAMETERS REFINEMENT ===
    // TODO: Implement instrument parameter refinement when Z-Rietveld API is discovered
    // These flags control refinement of diffractometer instrument profile parameters:
    // - refineInstrumentAlpha: peak shape parameter alpha
    // - refineInstrumentBeta: beta-0, beta-1, beta-q parameters
    // - refineInstrumentSig: sig-0, sig-1, sig-2, sig-q parameters
    // - refineInstrumentX, refineInstrumentY, refineInstrumentZ: sample positioning
    if (refineInstrumentAlpha || refineInstrumentBeta || refineInstrumentSig ||
        refineInstrumentX || refineInstrumentY || refineInstrumentZ) {
        std::cerr << "WARNING: Instrument parameter refinement not yet implemented. "
                  << "Requires Z-Rietveld ProfileFunction API discovery." << std::endl;
    }

    // === TOF FITTING RANGE ===
    // cfg keys: "tof_min" and/or "tof_max" (numeric strings, in microseconds)
    // Restricts the refinement window so Bragg-peak-dense low-TOF regions can be excluded.
    {
        std::string tofMinStr = trimCopy(getOrDefault("tof_min", ""));
        std::string tofMaxStr = trimCopy(getOrDefault("tof_max", ""));
        if (!tofMinStr.empty() || !tofMaxStr.empty()) {
            try {
                auto hists2 = rietveld->GetHistograms();
                if (hists2) {
                    for (auto &h : *hists2) {
                        if (!h) continue;
                        try {
                            auto fr = h->GetFittingRange();
                            if (!fr) {
                                fr = std::make_shared<ZRTFittingRange>();
                                h->SetFittingRange(fr);
                            }
                            fr->SetMode(ZRTFittingRangeModeDetail);
                            auto detail = fr->GetDetail();
                            if (!detail) {
                                detail = std::make_shared<ZRTFittingRangeDetail>();
                                fr->SetDetail(detail);
                                detail->SetFittingRange(fr.get());
                            }
                            if (!tofMinStr.empty()) {
                                try { detail->SetPeakRangeMin(std::stod(tofMinStr)); } catch(...) {}
                            }
                            if (!tofMaxStr.empty()) {
                                try { detail->SetPeakRangeMax(std::stod(tofMaxStr)); } catch(...) {}
                            }
                            std::cerr << "[tof_range] histogram='" << h->GetLabel()
                                      << "' tof_min=" << (tofMinStr.empty() ? "(keep)" : tofMinStr)
                                      << " tof_max=" << (tofMaxStr.empty() ? "(keep)" : tofMaxStr) << std::endl;
                        } catch(...) {}
                    }
                }
            } catch(...) {}
        }
    }

    // === ABSORPTION REFINEMENT ===
    if (refineAbsorption || hasAbsorptionCfg) {
        try {
            auto hists = rietveld->GetHistograms();
            if (hists) {
                for (auto &h : *hists) {
                    if (!h) continue;
                    try {
                        std::string histLabel = h->GetLabel();
                        bool refineThis = refineAbsorption;
                        std::string functionName;
                        bool hasAbsorptionValue = false;
                        double absorptionValue = 0.0;
                        bool hasPowderDensityValue = false;
                        double powderDensityValue = 0.0;

                        auto applyAbsorptionNode = [&](const nlohmann::json& node) {
                            if (node.is_boolean()) {
                                refineThis = node.get<bool>();
                                return;
                            }
                            if (!node.is_object()) return;

                            if (node.contains("refine")) {
                                if (node.at("refine").is_boolean()) {
                                    refineThis = node.at("refine").get<bool>();
                                } else if (node.at("refine").is_string()) {
                                    refineThis = toBool(node.at("refine").get<std::string>(), refineThis);
                                }
                            }
                            if (node.contains("function") && node.at("function").is_string()) {
                                functionName = node.at("function").get<std::string>();
                            }
                            if (node.contains("absorption") && node.at("absorption").is_number()) {
                                hasAbsorptionValue = true;
                                absorptionValue = node.at("absorption").get<double>();
                            }
                            if (node.contains("powder_density") && node.at("powder_density").is_number()) {
                                hasPowderDensityValue = true;
                                powderDensityValue = node.at("powder_density").get<double>();
                            }
                            if (node.contains("powderDensity") && node.at("powderDensity").is_number()) {
                                hasPowderDensityValue = true;
                                powderDensityValue = node.at("powderDensity").get<double>();
                            }
                        };

                        if (hasAbsorptionCfg) {
                            if (absorptionCfg.contains("_default")) {
                                applyAbsorptionNode(absorptionCfg.at("_default"));
                            }
                            if (absorptionCfg.contains(histLabel)) {
                                applyAbsorptionNode(absorptionCfg.at(histLabel));
                            }

                            // Compatibility for LLM-generated keys like "Histogram_A".
                            // Internal histogram label is typically "A", while reports print "Histogram_A".
                            const std::string prefixedLabel = std::string("Histogram_") + histLabel;
                            if (absorptionCfg.contains(prefixedLabel)) {
                                applyAbsorptionNode(absorptionCfg.at(prefixedLabel));
                            }
                            if (histLabel.rfind("Histogram_", 0) == 0 && histLabel.size() > std::string("Histogram_").size()) {
                                const std::string unprefixedLabel = histLabel.substr(std::string("Histogram_").size());
                                if (absorptionCfg.contains(unprefixedLabel)) {
                                    applyAbsorptionNode(absorptionCfg.at(unprefixedLabel));
                                }
                            }
                        }

                        auto dif = h->GetDiffractometer();
                        if (dif) {
                            auto ic = dif->GetIntensityCorrectionBase();
                            if (ic) {
                                if (!functionName.empty()) {
                                    auto type = absorptionTypeFromName(functionName);
                                    if (type != ZRTIntensityCorrectionAbsorptionTypeUnknown) {
                                        ic->SetAbsorptionType(type);
                                    } else {
                                        std::cerr << "WARNING: unknown absorption function '" << functionName
                                                  << "' on histogram '" << histLabel << "'." << std::endl;
                                    }
                                }
                                if (hasAbsorptionValue) {
                                    ic->SetAbsorption(absorptionValue);
                                }

                                // In this API, refine-able absorption-related parameter is PowderDensity (ZRTValueID).
                                auto powderDensity = ic->GetPowderDensity();
                                if (powderDensity) {
                                    if (hasPowderDensityValue) {
                                        try { powderDensity->SetValue(powderDensityValue); } catch(...) {}
                                    }
                                    try { powderDensity->SetID(refineThis ? ZRTIDVary : ZRTIDFixed); } catch(...) {}
                                    try { powderDensity->SetSelectMode(ZRTValueIDSelectModeVaryFixed); } catch(...) {}
                                    std::cerr << "[absorption][debug] histogram='" << histLabel
                                              << "' refine=" << (refineThis ? "true" : "false")
                                              << " function='" << (functionName.empty() ? "(keep)" : functionName) << "'"
                                              << " PowderDensityID=" << (refineThis ? "Vary" : "Fixed") << std::endl;
                                } else {
                                    std::cerr << "WARNING: refine_absorption requested but PowderDensity is null on histogram '"
                                              << histLabel << "'." << std::endl;
                                }
                            } else {
                                std::cerr << "WARNING: refine_absorption requested but IntensityCorrectionBase is null on histogram '"
                                          << histLabel << "'." << std::endl;
                            }
                        }
                    } catch(...) {}
                }
            }
        } catch(...) {}
    }

    try {
        auto cm = rietveld->GetCrystalModel();
        if (cm && cm->GetPhases()) {
            // === INITIAL CELL PARAMETER SETTER ===
            auto initialCellIt = cfg.find("initial_cell");
            if (initialCellIt != cfg.end() && !initialCellIt->second.empty()) {
                // Parse initial_cell: can be dict {'Phase_1': {'a':5.12,...}, 0: {...}}
                // or list [{'a':5.12,'b':5.12,...}, ...]
                // For simplicity, we'll support a basic format: "a:5.12,b:5.12,c:7.23,alpha:90,beta:90,gamma:120"
                // and apply to all phases (or parse more complex formats if needed)
                std::string cellStr = initialCellIt->second;
                std::map<std::string, double> cellParams;
                std::istringstream ss(cellStr);
                std::string token;
                while (std::getline(ss, token, ',')) {
                    auto colonPos = token.find(':');
                    if (colonPos != std::string::npos) {
                        std::string key = token.substr(0, colonPos);
                        std::string valStr = token.substr(colonPos + 1);
                        key.erase(0, key.find_first_not_of(" \t"));
                        key.erase(key.find_last_not_of(" \t") + 1);
                        valStr.erase(0, valStr.find_first_not_of(" \t"));
                        valStr.erase(valStr.find_last_not_of(" \t") + 1);
                        try {
                            double val = std::stod(valStr);
                            cellParams[key] = val;
                        } catch(...) {}
                    }
                }
                // Apply to first phase (or all phases if needed)
                if (!cellParams.empty() && cm->GetPhases()->size() > 0) {
                    auto phase = cm->GetPhases()->at(0);
                    if (phase) {
                        if (cellParams.find("a") != cellParams.end())
                            try { phase->GetLatticeConstantA()->SetValue(cellParams["a"]); } catch(...) {}
                        if (cellParams.find("b") != cellParams.end())
                            try { phase->GetLatticeConstantB()->SetValue(cellParams["b"]); } catch(...) {}
                        if (cellParams.find("c") != cellParams.end())
                            try { phase->GetLatticeConstantC()->SetValue(cellParams["c"]); } catch(...) {}
                        if (cellParams.find("alpha") != cellParams.end())
                            try { phase->GetLatticeConstantAlpha()->SetValue(cellParams["alpha"]); } catch(...) {}
                        if (cellParams.find("beta") != cellParams.end())
                            try { phase->GetLatticeConstantBeta()->SetValue(cellParams["beta"]); } catch(...) {}
                        if (cellParams.find("gamma") != cellParams.end())
                            try { phase->GetLatticeConstantGamma()->SetValue(cellParams["gamma"]); } catch(...) {}
                    }
                }
            }

            // === INITIAL MUSTRAIN SETTER ===
            auto initialMustrainIt = cfg.find("initial_mustrain");
            if (initialMustrainIt != cfg.end() && !initialMustrainIt->second.empty()) {
                // Parse initial_mustrain: can be single value ("1000.0"), list, or dict
                // For simplicity, treat as single float value applied to all phases
                try {
                    double mustrainVal = std::stod(initialMustrainIt->second);
                    // TODO: Set microstrain initial value in histogram/phase data
                    // Requires understanding of Z-Rietveld microstrain API
                    std::cerr << "INFO: initial_mustrain=" << mustrainVal
                              << " (API for setting microstrain not yet implemented)" << std::endl;
                } catch(...) {
                    std::cerr << "WARNING: Could not parse initial_mustrain value." << std::endl;
                }
            }

            // === INITIAL ATOM UISO SETTER ===
            std::map<std::string, double> initialUisoMap;
            auto initialUisoIt = cfg.find("initial_atom_uiso");
            if (initialUisoIt != cfg.end() && !initialUisoIt->second.empty()) {
                std::string uisoStr = initialUisoIt->second;
                // Try parsing as single float first
                try {
                    double singleVal = std::stod(uisoStr);
                    initialUisoMap["__all__"] = singleVal; // Special key for all atoms
                } catch(...) {
                    // Try parsing as dict: {'Si':0.012, 'O':0.015}
                    for (auto& c : uisoStr) if (c == '{' || c == '}' || c == '\'' || c == '"') c = ' ';
                    std::istringstream ss(uisoStr);
                    std::string token;
                    while (std::getline(ss, token, ',')) {
                        auto colonPos = token.find(':');
                        if (colonPos != std::string::npos) {
                            std::string atomLabel = token.substr(0, colonPos);
                            std::string valStr = token.substr(colonPos + 1);
                            atomLabel.erase(0, atomLabel.find_first_not_of(" \t"));
                            atomLabel.erase(atomLabel.find_last_not_of(" \t") + 1);
                            valStr.erase(0, valStr.find_first_not_of(" \t"));
                            valStr.erase(valStr.find_last_not_of(" \t") + 1);
                            try {
                                double val = std::stod(valStr);
                                initialUisoMap[atomLabel] = val;
                            } catch(...) {}
                        }
                    }
                }
            }

            for (auto &phase : *cm->GetPhases()) {
                if (!phase) continue;
                try { if (phase->GetMassRatio() && varyMassRatio) phase->GetMassRatio()->SetID(ZRTIDVary); } catch(...) {}
                if (varyLattice) {
                    // Use Z-Rietveld public API so crystal-system constraints are applied automatically:
                    // cubic -> a=b=c, tetragonal/hexagonal -> a=b, monoclinic/orthorhombic angle rules, etc.
                    try {
                        phase->SetLatticeConstantIntegratedID(ZRTIDVary);
                    } catch(...) {
                        std::cerr << "[lattice] WARNING: Failed to set lattice integrated ID for phase '"
                                  << phase->GetLabelForAnalyze() << "'" << std::endl;
                    }
                }

                // === SIZE AND MICROSTRAIN REFINEMENT ===
                if (refineSize) {
                    // TODO: Implement size refinement for this phase
                    // Requires Z-Rietveld API for crystallite size parameters
                    std::cerr << "WARNING: refine_size not yet implemented for phase " << phase->GetLabelForAnalyze() << std::endl;
                }
                if (refineMustrain) {
                    // TODO: Implement microstrain refinement for this phase
                    // Requires Z-Rietveld API for microstrain parameters
                    std::cerr << "WARNING: refine_mustrain not yet implemented for phase " << phase->GetLabelForAnalyze() << std::endl;
                }

                try {
                    if (phase->GetSites()) {
                        for (auto &site : *phase->GetSites()) {
                            if (!site) continue;

                            // Get site label for atom-specific refinement control
                            std::string siteLabel = site->GetLabel();

                            // Check if this atom has specific refinement instructions in refine_atoms
                            std::string atomFlags = "";
                            if (refineAtomsMap.find(siteLabel) != refineAtomsMap.end()) {
                                atomFlags = refineAtomsMap[siteLabel];
                            } else if (refineAtomsMap.find("all") != refineAtomsMap.end()) {
                                atomFlags = refineAtomsMap["all"];
                            }

                            // Apply initial Uiso value if specified
                            if (initialUisoMap.find("__all__") != initialUisoMap.end()) {
                                try {
                                    auto adp12 = site->GetAtomicDisplacement12();
                                    if (adp12) adp12->SetValue(initialUisoMap["__all__"]);
                                } catch(...) {}
                            } else if (initialUisoMap.find(siteLabel) != initialUisoMap.end()) {
                                try {
                                    auto adp12 = site->GetAtomicDisplacement12();
                                    if (adp12) adp12->SetValue(initialUisoMap[siteLabel]);
                                } catch(...) {}
                            }

                            // Atom-specific refinement control (refine_atoms dict)
                            if (!atomFlags.empty()) {
                                std::string flags = toLowerCopy(trimCopy(atomFlags));
                                auto has = [&](char c)->bool { return flags.find(c) != std::string::npos; };
                                auto hasWord = [&](const std::string& w)->bool { return flags.find(w) != std::string::npos; };

                                // Backward compatibility: legacy "X" means refine XYZ together.
                                bool legacyXMeansXYZ = has('x') && !has('y') && !has('z')
                                                     && !hasWord("xyz") && !hasWord("pos") && !hasWord("position");
                                bool refinePosAll = legacyXMeansXYZ || hasWord("xyz") || hasWord("pos") || hasWord("position");
                                bool refinePosX = refinePosAll || has('x');
                                bool refinePosY = refinePosAll || has('y');
                                bool refinePosZ = refinePosAll || has('z');
                                bool refineU = has('u') || hasWord("uiso");
                                bool refineF = has('f') || hasWord("occ") || hasWord("occupancy");

                                // Position refinement (X/Y/Z can be controlled independently)
                                if (refinePosX) {
                                    try { if (site->GetPositionX()) site->GetPositionX()->SetID(ZRTIDVary); } catch(...) {}
                                }
                                if (refinePosY) {
                                    try { if (site->GetPositionY()) site->GetPositionY()->SetID(ZRTIDVary); } catch(...) {}
                                }
                                if (refinePosZ) {
                                    try { if (site->GetPositionZ()) site->GetPositionZ()->SetID(ZRTIDVary); } catch(...) {}
                                }

                                // Uiso refinement (U)
                                if (refineU) {
                                    try {
                                        auto adp12 = site->GetAtomicDisplacement12();
                                        if (adp12) adp12->SetID(ZRTIDVary);
                                    } catch(...) {}
                                }

                                // Occupancy refinement (F)
                                if (refineF) {
                                    try { if (site->GetOccupancy()) site->GetOccupancy()->SetID(ZRTIDVary); } catch(...) {}
                                }
                            } else {
                                // Use global flags (backward compatibility)
                                if (varyPosition) {
                                    try { if (site->GetPositionX()) site->GetPositionX()->SetID(ZRTIDVary); } catch(...) {}
                                    try { if (site->GetPositionY()) site->GetPositionY()->SetID(ZRTIDVary); } catch(...) {}
                                    try { if (site->GetPositionZ()) site->GetPositionZ()->SetID(ZRTIDVary); } catch(...) {}
                                }
                                if (varyOccupancy) try { if (site->GetOccupancy()) site->GetOccupancy()->SetID(ZRTIDVary); } catch(...) {}
                                try {
                                    auto adp12 = site->GetAtomicDisplacement12();
                                    if (adp12) {
                                        if (fabs(adp12->GetValue()) > adpThreshold) adp12->SetID(ZRTIDVary);
                                        else adp12->SetID(ZRTIDFixed);
                                    }
                                } catch(...) {}
                            }
                        }
                    }
                } catch(...) {}
            }
        }
    } catch(...){}

    bool previewOnly = toBool(getOrDefault("preview_only", "false"), false);
    bool anyRefineFlag =
        scaleVary || refineBackground || refineProfileLevel0 || refineProfileLevel1 || refineProfileLevel2 ||
        refineAlpha || refineBeta0 || refineBeta1 || refineR0 ||
        varyMassRatio || varyLattice || varyOccupancy || varyPosition ||
        refineSize || refineMustrain || refineAbsorption || hasAbsorptionCfg ||
        refineInstrumentAlpha || refineInstrumentBeta || refineInstrumentSig ||
        refineInstrumentX || refineInstrumentY || refineInstrumentZ ||
        !refineAtomsMap.empty();

    // If no parameter is opened for refinement, default to preview mode (single run).
    if (!anyRefineFlag) {
        previewOnly = true;
    }

    // === APPLY DATA FILTERING FOR TOF RANGE ===
    // Before refinement, filter the histogram data to only include points within the specified tof range.
    // This ensures that the refinement only uses data within [tof_min, tof_max].
    {
        std::string tofMinStr = trimCopy(getOrDefault("tof_min", ""));
        std::string tofMaxStr = trimCopy(getOrDefault("tof_max", ""));
        
        if (!tofMinStr.empty() || !tofMaxStr.empty()) {
            double minThreshold = !tofMinStr.empty() ? std::stod(tofMinStr) : -std::numeric_limits<double>::infinity();
            double maxThreshold = !tofMaxStr.empty() ? std::stod(tofMaxStr) : std::numeric_limits<double>::infinity();
            
            try {
                auto hists = rietveld->GetHistograms();
                if (hists) {
                    for (auto &h : *hists) {
                        if (!h) continue;
                        try {
                            auto xs = h->GetXWaves();
                            auto ys = h->GetYIntensities();
                            auto errs = h->GetYErrors();
                            auto ybgs = h->GetYBackgrounds();
                            
                            if (!xs || !ys || xs->size() != ys->size()) continue;
                            
                            // Filter data points within [minThreshold, maxThreshold]
                            std::vector<double> filteredXs;
                            std::vector<double> filteredYs;
                            std::vector<double> filteredErrs;
                            std::vector<double> filteredYbgs;
                            
                            for (size_t i = 0; i < xs->size(); ++i) {
                                if ((*xs)[i] >= minThreshold && (*xs)[i] <= maxThreshold) {
                                    filteredXs.push_back((*xs)[i]);
                                    filteredYs.push_back((*ys)[i]);
                                    if (errs && i < errs->size()) {
                                        filteredErrs.push_back((*errs)[i]);
                                    }
                                    if (ybgs && i < ybgs->size()) {
                                        filteredYbgs.push_back((*ybgs)[i]);
                                    }
                                }
                            }
                            
                            // Replace the data with filtered versions
                            h->SetXWaves(&filteredXs);
                            h->SetYIntensities(&filteredYs);
                            if (!filteredErrs.empty()) {
                                h->SetYErrors(&filteredErrs);
                            }
                            if (!filteredYbgs.empty()) {
                                h->SetYBackgrounds(filteredYbgs);
                            }
                            
                            std::cerr << "[data_filter] histogram='" << h->GetLabel()
                                      << "' original_points=" << xs->size()
                                      << " filtered_points=" << filteredXs.size()
                                      << " range=[" << minThreshold << ", " << maxThreshold << "]" << std::endl;
                        } catch(...) {}
                    }
                }
            } catch(...) {}
        }
    }

    return runRefine(rietveld, outPathObj, previewOnly);
}

void autoRefine(const std::string& ZRTnPathStr)
{
    // read the generated zrietveldn
    shared_ptr<ZRTError> error = nullptr;
    std::shared_ptr<ZRTFilePath> outPathObj = ZRTFilePath::CreateWithUTF8String(ZRTnPathStr);
    shared_ptr<ZRTDocument> zrtnFile = ZRTDocument::CreateWithReadCborFile(outPathObj, &error);
    if (ZRTError::HasError(&error) || !zrtnFile) {
        if (error) cout << *error->GetAllMessage() << endl;
        return;
    }

    // Create and configure the Rietveld object from the read document
    shared_ptr<ZRTRietveld> rietveld = zrtnFile->GetCurrentRietveld();
    rietveld->SetHistoryIndex(zrtnFile->GetCurrentHistoryIndex());
    shared_ptr<ZRTPreference> preference = ZRTPreference::CreateForDefault();
    preference->SetNeedLocalLog(false);
    rietveld->SetPreference(preference);
    shared_ptr<ZRTRunManager> runManager = ZRTRunManager::CreateForDefault();
    rietveld->SetRunManager(runManager);
    rietveld->SetDocument(zrtnFile.get());
    
    
    //get the histograms number and phases number for the selection of auto analyze mode 
    auto hs = rietveld->GetHistograms();
    size_t histCount = (hs ? hs->size() : 0);
    size_t phaseCount = (rietveld->GetCrystalModel() && rietveld->GetCrystalModel()->GetPhases()) ? rietveld->GetCrystalModel()->GetPhases()->size() : 0;

    //prepare autoAnalyze
    shared_ptr<ZRTAutoAnalyze> autoAnalyze = make_shared<ZRTAutoAnalyze>();

    if (histCount > 1) {
        if (phaseCount <= 1) {
            autoAnalyze->SetSteps(ZRTAutoAnalyze::GetPresetSteps(ZRTAutoAnalyzePresetMultiPointOnePhase));
            } else {
                autoAnalyze->SetSteps(ZRTAutoAnalyze::GetPresetSteps(ZRTAutoAnalyzePresetMultiPointMultiPhase));
            }
        }
    else {
        if (phaseCount <= 1) {
            autoAnalyze->SetSteps(ZRTAutoAnalyze::GetPresetSteps(ZRTAutoAnalyzePresetOnePointOnePhase));
            }
        else {
            autoAnalyze->SetSteps(ZRTAutoAnalyze::GetPresetSteps(ZRTAutoAnalyzePresetOnePointMultiPhase));
        }
    }
    rietveld->SetAutoAnalyze(autoAnalyze);
    autoAnalyze->SetUse(true);
    rietveld->SetUseDefaultDebugDescription(true); 

    (void)runRefine(rietveld, outPathObj, false);
}

static std::string ExtractResultSectionFromRunLog(const std::string& fullLog)
{
    if (fullLog.empty()) return fullLog;

    size_t start = fullLog.find("Outputting...");
    if (start == std::string::npos) {
        start = fullLog.find("* S^2(Chi-square), Degree_of_freedom, Number_of_iteration");
    }
    if (start == std::string::npos) {
        start = fullLog.find("* Histogram_");
    }
    if (start == std::string::npos) {
        return fullLog;
    }

    size_t end = std::string::npos;
    size_t historyOutput = fullLog.find("\n-- History --", start);
    if (historyOutput != std::string::npos) {
        end = historyOutput;
    }

    size_t finished = fullLog.find("---> Analysis finished <---", start);
    if (finished != std::string::npos) {
        size_t finishedLineEnd = fullLog.find('\n', finished);
        size_t finishedEnd = (finishedLineEnd == std::string::npos) ? fullLog.size() : (finishedLineEnd + 1);
        if (end == std::string::npos || finishedEnd < end) {
            end = finishedEnd;
        }
    }

    if (end == std::string::npos) {
        end = fullLog.size();
    }
    return fullLog.substr(start, end - start);
}

std::map<std::string,std::string> runRefine(shared_ptr<ZRTRietveld> rietveld, shared_ptr<ZRTFilePath> outPathObj, bool singleRun)
{
    std::map<std::string,std::string> result;
    result["ok"] = "false";
    result["fit_status"] = "unknown";

    // excute the first run (simulation)
    shared_ptr<ZRTError> error = nullptr;
    ZRTRietveldFinishedMode finishedMode = rietveld->Run(&error);
    if (ZRTError::HasError(&error)) {
        cout << "Run error (first run): " << *error->GetAllMessage() << endl;
        result["fit_status"] = "run_error_first";
        result["error"] = *error->GetAllMessage();
        return result;
    }

    // check whether a second run is needed for fitting
    if (!singleRun && finishedMode == ZRTRietveldFinishedModeSimulation) {
        cout << "First run completed (Simulation). Running again for Fitting..." << endl;

        // 第二次运行进行实际的精修
        error = nullptr;
        finishedMode = rietveld->Run(&error);
        if (ZRTError::HasError(&error)) {
            cout << "Run error (second run): " << *error->GetAllMessage() << endl;
            result["fit_status"] = "run_error_second";
            result["error"] = *error->GetAllMessage();
            return result;
        }
    }

    if (finishedMode == ZRTRietveldFinishedModeFitting) {
        cout << "Refinement completed successfully!" << endl;
        // Print summary statistics (if available)
        try {
            auto stats = rietveld->GetChiSquareAndR();
            if (stats) {
                cout.setf(std::ios::fixed); cout<<setprecision(6);
                cout << "Summary fit statistics:" << endl;
                cout << " ChiSquare = " << stats->GetChiSquare() << endl;
                cout << " Rwp       = " << stats->GetRwp() << endl;
                cout << " Re        = " << stats->GetRe() << endl;
                cout << " DegreesOfFreedom = " << stats->GetDegreeOfFreedom() << endl;
                cout << " NumberOfIndependent = " << stats->GetNumberOfIndependent() << endl;
            } else {
                cout << "No global ChiSquare/R available." << endl;
            }
        } catch(...) {
            cout << "Failed to retrieve fit statistics." << endl;
        }
    }

    // Export fit/bragg CSVs for Python-side plotting in both simulation and fitting modes.
    try {
        auto hists = rietveld->GetHistograms();
        if (hists && 0 < hists->size()) {
            std::filesystem::path outDir = outPathObj->GetPath().parent_path();
            std::vector<std::string> csvPaths;
            std::vector<std::string> braggCsvPaths;

            std::vector<std::string> phaseLabels;
            try {
                auto crystalModel = rietveld->GetCrystalModel();
                auto phases = crystalModel ? crystalModel->GetPhases() : nullptr;
                if (phases) {
                    for (size_t pi = 0; pi < phases->size(); ++pi) {
                        auto phase = phases->at(pi);
                        if (phase) {
                            phaseLabels.push_back(phase->GetLabelForGlobal());
                        } else {
                            phaseLabels.push_back(std::string("Phase ") + std::to_string(pi + 1));
                        }
                    }
                }
            } catch(...) {
            }

            for (size_t hi = 0; hi < hists->size(); ++hi) {
                auto h = hists->at(hi);
                if (!h) continue;
                const vector<double>* xs = h->GetXWaves();
                const vector<double>* ys = h->GetYIntensities();
                const vector<double>* yc = h->GetYCals();
                const vector<double>* yb = h->GetYBackgrounds();
                if (!xs || !ys || !yc) continue;
                if (xs->size() != ys->size() || xs->size() != yc->size()) continue;
                bool hasBackground = (yb && yb->size() == xs->size());
                std::string label = h->GetLabel();
                std::string csvPath = (outDir / (label + std::string("_fit.csv"))).string();
                
                // Extract tof_min and tof_max from FittingRange if set.
                double tofMinVal = std::numeric_limits<double>::quiet_NaN();
                double tofMaxVal = std::numeric_limits<double>::quiet_NaN();
                try {
                    auto fr = h->GetFittingRange();
                    if (fr && fr->GetMode() == ZRTFittingRangeModeDetail) {
                        auto detail = fr->GetDetail();
                        if (detail) {
                            tofMinVal = detail->GetPeakRangeMin();
                            tofMaxVal = detail->GetPeakRangeMax();
                        }
                    }
                } catch(...) {}
                
                std::ofstream ofs(csvPath);
                // Write metadata comments at the top.
                ofs << "# tof_min=" << tofMinVal << "\n";
                ofs << "# tof_max=" << tofMaxVal << "\n";
                ofs << "x,y_obs,y_calc,y_bkg,residual\n";
                
                // Apply tof range filtering if tof_min and/or tof_max are set
                bool hasRangeLimit = !std::isnan(tofMinVal) || !std::isnan(tofMaxVal);
                double minForFilter = hasRangeLimit && !std::isnan(tofMinVal) ? tofMinVal : -std::numeric_limits<double>::infinity();
                double maxForFilter = hasRangeLimit && !std::isnan(tofMaxVal) ? tofMaxVal : std::numeric_limits<double>::infinity();
                
                for (size_t i = 0; i < xs->size(); ++i) {
                    // Skip points outside the tof range if filtering is active
                    if (hasRangeLimit && ((*xs)[i] < minForFilter || (*xs)[i] > maxForFilter)) {
                        continue;
                    }
                    double ybVal = hasBackground ? (*yb)[i] : 0.0;
                    double residual = (*ys)[i] - (*yc)[i];
                    ofs << (*xs)[i] << "," << (*ys)[i] << "," << (*yc)[i] << "," << ybVal << "," << residual << "\n";
                }
                ofs.close();
                csvPaths.push_back(csvPath);

                try {
                    const auto* allPeaks = h->GetAllPhaseOriginalPeakPositions();
                    if (allPeaks && !allPeaks->empty()) {
                        std::string braggCsvPath = (outDir / (label + std::string("_bragg.csv"))).string();
                        std::ofstream bof(braggCsvPath);
                        bof << "phase_index,phase_label,position\n";

                        for (size_t ip = 0; ip < allPeaks->size(); ++ip) {
                            const std::vector<double>* phasePeaks = allPeaks->at(ip);
                            if (!phasePeaks || phasePeaks->empty()) continue;

                            std::string phaseLabel = (ip < phaseLabels.size() && !phaseLabels[ip].empty())
                                ? phaseLabels[ip]
                                : (std::string("Phase ") + std::to_string(ip + 1));
                            std::replace(phaseLabel.begin(), phaseLabel.end(), ',', ' ');

                            for (size_t k = 0; k < phasePeaks->size(); ++k) {
                                bof << ip << "," << phaseLabel << "," << phasePeaks->at(k) << "\n";
                            }
                        }
                        bof.close();
                        braggCsvPaths.push_back(braggCsvPath);
                    }
                } catch(...) {
                }
            }

            if (!csvPaths.empty()) {
                std::string joined;
                for (size_t i = 0; i < csvPaths.size(); ++i) {
                    if (0 < i) joined += ";";
                    joined += csvPaths[i];
                }
                result["fit_data_csvs"] = joined;
            }

            if (!braggCsvPaths.empty()) {
                std::string joinedBragg;
                for (size_t i = 0; i < braggCsvPaths.size(); ++i) {
                    if (0 < i) joinedBragg += ";";
                    joinedBragg += braggCsvPaths[i];
                }
                result["bragg_csvs"] = joinedBragg;
            }
        }
    } catch(...) {
        std::cerr << "Failed to export fit result CSVs." << std::endl;
    }

    result["fit_status"] = (finishedMode == ZRTRietveldFinishedModeFitting) ? "fitting" : "simulation";
    result["ok"] = "true";

    try {
        ZRTDocument* doc = rietveld ? rietveld->GetDocument() : nullptr;
        if (doc) {
            int32_t historyIndex = doc->GetCurrentHistoryIndex();
            auto logString = doc->GetLogStringWithHistoryIndex(historyIndex);
            if (logString) {
                const std::string fullLog = *logString;
                const std::string resultLog = ExtractResultSectionFromRunLog(fullLog);
                result["history_index"] = std::to_string(historyIndex);

                if (outPathObj) {
                    std::filesystem::path reportPath = outPathObj->GetPath();
                    reportPath.replace_extension(".txt");
                    std::ofstream of(reportPath.string());
                    if (of.is_open()) {
                        of << resultLog;
                        of.close();
                        result["report_path"] = reportPath.string();
                    }
                }
            }
        }
    } catch(...) {
    }

    return result;
}



static std::string PrepareHistogramFileForZRT(const std::string& inputPathStr)
{
    std::filesystem::path inputPath(inputPathStr);
    if (!std::filesystem::exists(inputPath)) {
        throw std::runtime_error("Histogram file does not exist: " + inputPathStr);
    }

    // Native supported path.
    if (inputPath.extension() == ".histogramIgor") {
        return inputPathStr;
    }

    std::ifstream in(inputPathStr);
    if (!in.is_open()) {
        throw std::runtime_error("Failed to open histogram file for format adaptation: " + inputPathStr);
    }

    std::string firstToken;
    {
        std::string line;
        while (std::getline(in, line)) {
            std::istringstream ss(line);
            if (ss >> firstToken) {
                break;
            }
        }
    }
    in.clear();
    in.seekg(0, std::ios::beg);

    std::filesystem::path outPath = inputPath.parent_path() / (inputPath.stem().string() + ".histogramIgor");
    std::ofstream out(outPath.string());
    if (!out.is_open()) {
        throw std::runtime_error("Failed to create converted histogram file: " + outPath.string());
    }

    if (firstToken == "IGOR") {
        // File content is IGOR-like but extension is unsupported by Z-Rietveld.
        out << in.rdbuf();
        return outPath.string();
    }

    // Treat as plain 3-column text: x y yerr.
    out << "IGOR\n";
    out << "WAVES/O tof yint yerr\n";
    out << "BEGIN\n";

    std::string line;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        std::replace(line.begin(), line.end(), ',', ' ');
        std::istringstream ss(line);
        double x = 0.0, y = 0.0, e = 0.0;
        if (ss >> x >> y >> e) {
            out << x << " " << y << " " << e << "\n";
        }
    }
    out << "END\n";

    return outPath.string();
}


std::string
generateZrietveldnFile(
    const std::string& histPathStr,
    const std::string& instrPathStr,
    const std::vector<std::string>& cifPathStrs,
    const std::string& outputPathStr = "")
{
    std::string adaptedHistPathStr = PrepareHistogramFileForZRT(histPathStr);

    // Convert python-friendly inputs (strings) to ZRTFilePath objects
    shared_ptr<ZRTFilePath> histPath = ZRTFilePath::CreateWithUTF8String(adaptedHistPathStr);
    shared_ptr<ZRTFilePath> instrPath = ZRTFilePath::CreateWithUTF8String(instrPathStr);

    auto doc = std::make_shared<ZRTDocument>();

    // 1) Append histogram and diffractometer
    doc->AppendInputHistogram("A", histPath);

    // Append diffractometer, throw on error
    {
        shared_ptr<ZRTError> err = nullptr;
        doc->AppendZDiffractometer("A", instrPath, &err);
        if (ZRTError::HasError(&err)) {
            std::string detail = "AppendZDiffractometer failed";
            if (err) {
                detail += " [type=" + err->GetTypeString() + "]";
                auto allMessage = err->GetAllMessage();
                if (allMessage && !allMessage->empty()) {
                    detail += ": " + *allMessage;
                } else {
                    std::string desc = err->Description(true);
                    if (!desc.empty()) {
                        detail += ": " + desc;
                    }
                }
            }
            throw std::runtime_error(detail);
        }
    }

    // 2) Append cif files

	// Create a rietveld to hold the crystal model
    auto rietveld = ZRTRietveld::CreateForDefault();
    
    // Create a crystal model to hold the phases from cif files
    auto crystalModel = make_shared<ZRTCrystalModel>();

    // Loop through each cif file path string, create ZRTFilePath and read into phase
    for (const auto& cifPathStr : cifPathStrs) {
        auto cifPath = ZRTFilePath::CreateWithUTF8String(cifPathStr);
        auto phase = ZRTPhase::CreateForDefault();
        // check space group
        {
            shared_ptr<ZRTError> err = nullptr;
            ZRTSpaceGroupEvaluation eval;
            auto sg = ZRTPhase::CheckSpaceGroupEvaluation(cifPath, &eval, &err);
            if (ZRTError::HasError(&err)) {
                throw std::runtime_error(std::string("CheckSpaceGroupEvaluation failed: ") + *err->GetAllMessage());
            }
            ZRTSpaceGroupNumber sgNum = sg->GetSpaceGroupNumber();
            shared_ptr<ZRTError> err2 = nullptr;
            phase->ReadCifFile(cifPath, ZRTSiteAtomicDisplacementUnit(0), sgNum, &err2);
            if (ZRTError::HasError(&err2)) {
                throw std::runtime_error(std::string("ReadCifFile failed for ") + cifPathStr + ": " + *err2->GetAllMessage());
            }
        }
        phase->SetLabel(ZRTFilePath::CreateWithUTF8String(cifPathStr)->GetFileNameStem());
        phase->GetMassRatio()->SetValue(1.0);

        // add the phase to the crystal model
        phase->SetCrystalModel(crystalModel.get());
        crystalModel->GetPhases()->push_back(phase);
    }

	//normalize the mass ratios of the phases so that they sum to 1.0
    double sum = 0;
    for (auto& p : *crystalModel->GetPhases()) sum += p->GetMassRatio()->GetValue();
    if (sum > 0) {
        for (auto& p : *crystalModel->GetPhases()) p->GetMassRatio()->SetValue(p->GetMassRatio()->GetValue() / sum);
    }

	// Set the crystal model to the rietveld
	rietveld->SetCrystalModel(crystalModel);
    crystalModel->SetRietveld(rietveld.get());

    // Generate constraints string from the crystal model and append to the document
    // This ensures the created document contains the same constraint block as a typical existing project
    shared_ptr<ZRTError> cerr = nullptr;
    auto constraint = crystalModel->CreateAllConstraint(&cerr);
    if (constraint && !constraint->empty()) {
        // store constraint into the crystal model and document so IsValidConstraint() can see it
        crystalModel->SetAllConstraint(constraint);
    }
    else if (ZRTError::HasError(&cerr)) {
        throw std::runtime_error(std::string("CreateAllConstraint failed: ") + *cerr->GetAllMessage());
    }
    
    
    // 3) register the histogram and diffractometer to the rietveld
    {
        shared_ptr<ZRTError> err = nullptr;
        auto hist = rietveld->AppendHistogramFileAndDiffractometerFile(histPath, instrPath, &err);
        if (ZRTError::HasError(&err)) {
            std::ostringstream oss;
            oss << "[generateZrietveldnFile][Step3:RegisterHistogramAndDiffractometer] "
                << "AppendHistogramFileAndDiffractometerFile failed";
            oss << "\n  histPathInput: " << histPathStr;
            oss << "\n  histPathUsed: " << adaptedHistPathStr;
            oss << "\n  instrPath: " << instrPathStr;
            if (err) {
                oss << "\n  errorType: " << err->GetTypeString();
                auto allMessage = err->GetAllMessage();
                if (allMessage && !allMessage->empty()) {
                    oss << "\n  allMessage: " << *allMessage;
                }
                std::string desc = err->Description(true);
                if (!desc.empty()) {
                    oss << "\n  description: " << desc;
                }
            }
            throw std::runtime_error(oss.str());
        }
        if (!hist) {
            std::ostringstream oss;
            oss << "[generateZrietveldnFile][Step3:RegisterHistogramAndDiffractometer] "
                << "AppendHistogramFileAndDiffractometerFile returned null histogram without ZRTError";
            oss << "\n  histPathInput: " << histPathStr;
            oss << "\n  histPathUsed: " << adaptedHistPathStr;
            oss << "\n  instrPath: " << instrPathStr;
            throw std::runtime_error(oss.str());
        }
        hist->SetLabel("A");
    }


    // Use library default convergence settings and only pin max cycle.
    auto conv = ZRTConvergenceCondition::CreateForDefault();
    if (conv) {
        auto sel = conv->GetSelectedModeObject();
        if (sel) {
            sel->SetMaxCycle(10);
        }
    }

    // 最后把 convergenceCondition 绑到 rietveld
    rietveld->SetConvergenceCondition(conv);

	// 4）relate the rietveld to the document
    rietveld->SetDocument(doc.get());
    

    // 5) initial the relation between hitogram and phases
    rietveld->InitializePhaseAndHistograms();

	// 6) Append the rietveld to the document
	doc->AppendRietveldCurrent(rietveld);

	// 7) Save the document to a .zrietveldn file
    shared_ptr<ZRTFilePath> outputPath;
    if (!outputPathStr.empty()) {
        std::filesystem::path outFs(outputPathStr);
        auto parent = outFs.parent_path();
        if (!parent.empty()) {
            std::error_code ec;
            std::filesystem::create_directories(parent, ec);
            if (ec) {
                throw std::runtime_error(std::string("create_directories failed: ") + ec.message());
            }
        }
        outputPath = ZRTFilePath::CreateWithUTF8String(outputPathStr);
    }
    else {
        outputPath = histPath->DeleteLastPathComponent()->Append(histPath->GetFileNameStem() + ".zrietveldn");
    }
    {
        shared_ptr<ZRTError> err = nullptr;
        doc->SaveToCborFile(outputPath, nullptr, &err);
        if (ZRTError::HasError(&err)) {
            throw std::runtime_error(std::string("SaveToCborFile failed: ") + *err->GetAllMessage());
        }
    }

    return outputPath->GetPath().string();
    }

std::string exportCifFromZrtn(
    const std::string& zrtnPathStr,
    const std::string& outputCifPathStr = "",
    const std::string& phaseLabel = "")
{
    shared_ptr<ZRTError> error = nullptr;
    auto zrtnPath = ZRTFilePath::CreateWithUTF8String(zrtnPathStr);
    auto doc = ZRTDocument::CreateWithReadCborFile(zrtnPath, &error);
    if (ZRTError::HasError(&error) || !doc) {
        std::string detail = "Failed to open zrietveldn file";
        if (error && error->GetAllMessage()) {
            detail += ": " + *error->GetAllMessage();
        }
        throw std::runtime_error(detail);
    }

    auto rietveld = doc->GetCurrentRietveld();
    if (!rietveld) {
        throw std::runtime_error("No current rietveld object found in zrietveldn file");
    }

    auto crystalModel = rietveld->GetCrystalModel();
    if (!crystalModel || !crystalModel->GetPhases() || crystalModel->GetPhases()->empty()) {
        throw std::runtime_error("No phase found in zrietveldn file");
    }

    shared_ptr<ZRTPhase> selectedPhase = nullptr;
    if (phaseLabel.empty()) {
        selectedPhase = crystalModel->GetPhases()->at(0);
    }
    else {
        for (const auto& phase : *crystalModel->GetPhases()) {
            if (!phase) continue;
            if (phase->GetLabelForAnalyze() == phaseLabel || phase->GetLabelForGlobal() == phaseLabel) {
                selectedPhase = phase;
                break;
            }
        }
        if (!selectedPhase) {
            std::string available;
            for (const auto& phase : *crystalModel->GetPhases()) {
                if (!phase) continue;
                if (!available.empty()) available += ", ";
                available += phase->GetLabelForAnalyze();
            }
            throw std::runtime_error("Phase label not found: " + phaseLabel + ". Available phases: [" + available + "]");
        }
    }

    const std::string cifContent = selectedPhase->GetCifString();
    if (cifContent.empty()) {
        throw std::runtime_error("Generated CIF content is empty");
    }

    std::filesystem::path outPath;
    if (!outputCifPathStr.empty()) {
        outPath = std::filesystem::path(outputCifPathStr);
    }
    else {
        const std::string label = selectedPhase->GetLabelForAnalyze().empty() ? "phase" : selectedPhase->GetLabelForAnalyze();
        std::filesystem::path basePath(zrtnPathStr);
        outPath = basePath.parent_path() / (basePath.stem().string() + "_" + label + ".cif");
    }

    if (outPath.extension() != ".cif") {
        outPath.replace_extension(".cif");
    }

    std::error_code ec;
    auto parent = outPath.parent_path();
    if (!parent.empty()) {
        std::filesystem::create_directories(parent, ec);
        if (ec) {
            throw std::runtime_error(std::string("Failed to create output directory: ") + ec.message());
        }
    }

    std::ofstream out(outPath.string());
    if (!out.is_open()) {
        throw std::runtime_error("Failed to open output CIF file: " + outPath.string());
    }
    out << cifContent;
    out.close();

    return outPath.string();
}


    
#if defined(BUILD_PYBIND)
PYBIND11_MODULE(refineTool, m) {
    m.doc() = "RefineTool pybind11 bindings";
    m.def(
        "genZRTn",
        &generateZrietveldnFile,
        "Generate .zrietveldn file from paths: hist, instr, [cif,...], optional output path",
        py::arg("histPathStr"),
        py::arg("instrPathStr"),
        py::arg("cifPathStrs"),
        py::arg("outputPathStr") = "");
    m.def("refine", &ManualRefinementWithDict, "Perform manual refinement with a configuration dictionary");
    m.def("autoRefine", &autoRefine, "Perform auto refinement on a .zrietveldn file");
    m.def(
        "exportCif",
        &exportCifFromZrtn,
        "Export a phase from .zrietveldn file to CIF. If phaseLabel is empty, export the first phase.",
        py::arg("zrtnPathStr"),
        py::arg("outputCifPathStr") = "",
        py::arg("phaseLabel") = "");
}
#endif

// Standalone test harness. This main allows compiling and running the binary
// to validate generateZrietveldnFile independently of Python.
#if !defined(BUILD_PYBIND)
int main(int argc, char** argv) {
   try {
    std::string hist = "testData/RUN0001094_groupBM.dat";
    std::string instr = "testData/BM.zDiffractometer";
    std::vector<std::string> cifs = { "testData/LNO.cif" };

        // Allow overriding via command line: hist instr cif1 cif2 ...
        if (argc >= 3) {
            hist = argv[1];
            instr = argv[2];
            cifs.clear();
            for (int i = 3; i < argc; ++i) cifs.push_back(argv[i]);
        }

        std::string outPath = generateZrietveldnFile(hist, instr, cifs);
        std::cout << "generated the project file: " << outPath << std::endl;

        // Configure manual refinement parameters (background, mass ratios, lattice, sites...)
        std::map<std::string,std::string> def;
        def["scaleVary"] = "true";
        //def["scaleInit"] = "1.0";
        def["bgOrder"] = "6";
        def["bgInit"] = "1.0,0,0,0,0,0";
        def["bgMode"] = "legendre";
        def["refine_profile_level0"] = "false";
        def["refine_profile_level1"] = "false";
        def["refine_profile_level2"] = "false";
        def["refine_profile_alpha"] = "true";
        def["refine_profile_beta0"] = "false";
        def["refine_profile_beta1"] = "false";
        def["refine_profile_r0"] = "false";
        def["varyMassRatio"] = "false";
        def["varyLattice"] = "true";
        def["varyOccupancy"] = "false";
        def["adpVaryThreshold"] = "0.0";
        def["maxCycle"] = "6";
        def["refine_absorption"] = "true";
        // Phase label comes from CIF file stem. For testData/LNO.cif the label is "LNO".
        def["space_group"] = "{'LNO':'166_1'}";
        ManualRefinementWithDict(outPath, def);

        // autoRefine(outPath);
       return 0;
   }
   catch (const std::exception& e) {
       std::cerr << "Error: " << e.what() << std::endl;
       return 1;
   }
}
#endif
