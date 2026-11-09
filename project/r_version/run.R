# Usage: Rscript --vanilla run.R [--research] [--repeat 0] [--fold 0]
# Default: exact frozen-Python-model scoring and all 216 financial scenarios.
args_all <- commandArgs(FALSE);script_arg<-grep("^--file=",args_all,value=TRUE)
root<-if(length(script_arg))dirname(normalizePath(sub("^--file=","",script_arg[1]))) else getwd()
setwd(root);Sys.setlocale("LC_COLLATE","C")
source("config.R",encoding="UTF-8");source("pricing_engine.R",encoding="UTF-8");source("native_models.R",encoding="UTF-8")
args<-commandArgs(TRUE);dir.create("results",showWarnings=FALSE)
checks<-read.csv("input_checksums.csv",stringsAsFactors=FALSE)
if(any(unname(tools::md5sum(checks$file))!=checks$md5))stop("Input checksum mismatch")
model<-read_portable_model("portable_model");q<-read.csv("data/quotes.csv",stringsAsFactors=FALSE,check.names=FALSE)
prices<-quote_portable(model,q);write.csv(prices,"results/r_exact_quote_detail.csv",row.names=FALSE,na="")
write.csv(data.frame(PolicyID=prices$PolicyID,Premium=round(prices$quote,2)),"results/r_exact_submission.csv",row.names=FALSE)
c<-config$commercial;c$net_premium_ratio<-1-config$pricing$variable_expense_ratio-config$pricing$tax_ratio
commercial<-commercial_grid(prices,c)
write.csv(commercial$grid,"results/r_exact_commercial_scenarios.csv",row.names=FALSE)
write.csv(commercial$robust,"results/r_exact_commercial_robust.csv",row.names=FALSE)
write.csv(data.frame(feasible=commercial$feasible,selected_adjustment=commercial$selected_adjustment),"results/r_exact_commercial_decision.csv",row.names=FALSE,na="")
reference<-read.csv("portable_model/python_quote_detail.csv",stringsAsFactors=FALSE)
errors<-sapply(c("pure_premium","quote","cost_floor","expected_contribution"),function(n)max(abs(prices[[n]]-reference[[n]])))
stopifnot(max(errors)<1e-8)
write.csv(data.frame(field=names(errors),maximum_absolute_error=errors),"results/python_r_scoring_parity.csv",row.names=FALSE)
saveRDS(model,"results/frozen_selected_model.rds")
writeLines(c(capture.output(sessionInfo()),"","Native GBM implementation: rpart Newton boosting, not sklearn histogram boosting."),"results/R_SESSION_INFO.txt")
cat("Exact imported-model scoring complete. Max quote error:",max(errors),"\n")
if("--research"%in%args) {
  source("research_pipeline.R",encoding="UTF-8")
  value_arg<-function(name){i<-match(name,args);if(is.na(i))NULL else as.integer(args[i+1])}
  run_research(config,value_arg("--repeat"),value_arg("--fold"))
}
source("diagnostics.R",encoding="UTF-8")
run_diagnostics(config,model,prices,commercial)
