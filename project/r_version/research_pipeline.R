# Original development fold manifest is authoritative. Previously viewed test
# outcomes are not included in the exported R data or native fitting cycle.
run_research <- function(config,only_repeat=NULL,only_fold=NULL) {
  set.seed(config$seed);all<-read.csv("data/training_and_calibration.csv",stringsAsFactors=FALSE,check.names=FALSE)
  dev<-all[all$split%in%c("train","validation"),];level<-all[all$split=="calibration",]
  manifest<-read.csv("data/development_fold_manifest.csv",stringsAsFactors=FALSE,check.names=FALSE)
  cache_dir<-Sys.getenv("MOTOR_R_CACHE",".native_cache");dir.create(cache_dir,showWarnings=FALSE,recursive=TRUE);rows<-list();counter<-0
  # Hash the actual source/config/input identities using base R MD5, not just a filename.
  signature<-paste(c(unname(tools::md5sum(c("native_models.R","pricing_engine.R","research_pipeline.R","config.R","data/training_and_calibration.csv","data/development_fold_manifest.csv"))),R.version.string,as.character(packageVersion("rpart"))),collapse="_")
  signature_file<-tempfile(tmpdir=cache_dir);writeLines(signature,signature_file);cache_id<-unname(tools::md5sum(signature_file));unlink(signature_file)
  for(rep in 0:(config$cv$repeats-1))for(fold in 0:(config$cv$folds-1)) {
    if(!is.null(only_repeat)&&rep!=only_repeat)next
    if(!is.null(only_fold)&&fold!=only_fold)next
    part<-manifest[manifest[["repeat"]]==rep&manifest$fold==fold,]
    ix<-lapply(c("fit","cal","val"),function(role)match(part$PolicyID[part$role==role],dev$PolicyID));names(ix)<-c("fit","cal","val")
    stopifnot(all(lengths(ix)>30))
    stopifnot(!anyNA(unlist(ix)),!length(intersect(ix$fit,ix$cal)),!length(intersect(c(ix$fit,ix$cal),ix$val)))
    fit<-dev[ix$fit,];cal<-dev[ix$cal,];val<-dev[ix$val,]
    cat("Native R repeat",rep+1,"fold",fold+1,"\n");flush.console()
    for(name in config$candidates) {
      file<-file.path(cache_dir,paste(cache_id,rep,fold,name,sep="_"))
      if(file.exists(file))m<-readRDS(file) else {m<-fit_native_model(name,fit,config);saveRDS(m,file)}
      pc<-predict_native_model(m,cal);pv<-predict_native_model(m,val)
      for(mode in config$calibration_modes) {
        adjustment<-calibrate_level(cal$ClaimAmount,pc,mode,config$cv$log_calibration_prior_sd)
        counter<-counter+1;score<-loss_metrics(val$ClaimAmount,pv*adjustment$factor)
        rows[[counter]]<-data.frame(candidate=paste(name,mode,sep="::"),model=name,calibration_mode=mode,complexity=unname(complexity[name]),
          repeat_id=rep,fold=fold,calibration_factor=adjustment$factor,t(as.matrix(score)),check.names=FALSE)
      }
      write.csv(do.call(rbind,rows),"results/native_r_cv_fold_metrics.csv",row.names=FALSE)
    }
  }
  scores<-do.call(rbind,rows)
  summary<-do.call(rbind,lapply(split(scores,scores$candidate),function(g)data.frame(candidate=g$candidate[1],model=g$model[1],calibration_mode=g$calibration_mode[1],complexity=g$complexity[1],
    mean_deviance=mean(g$tweedie_deviance_p1.5),sd_deviance=sd(g$tweedie_deviance_p1.5),folds=nrow(g),mean_AE=mean(g$actual_over_expected))))
  summary$heuristic_se<-summary$sd_deviance/sqrt(summary$folds);summary<-summary[order(summary$mean_deviance,summary$complexity,summary$candidate),]
  write.csv(summary,"results/native_r_cv_model_summary.csv",row.names=FALSE)
  if(is.null(only_repeat)&&is.null(only_fold)) {
    nonconstant<-summary[summary$model!="Constant",];best<-nonconstant[1,];eligible<-nonconstant[nonconstant$mean_deviance<=best$mean_deviance+best$heuristic_se,]
    selected<-eligible[order(eligible$complexity,eligible$mean_deviance,eligible$candidate),][1,]
    final<-fit_native_model(selected$model,dev,config);adjustment<-calibrate_level(level$ClaimAmount,predict_native_model(final,level),selected$calibration_mode,config$cv$log_calibration_prior_sd)
    q<-read.csv("data/quotes.csv",stringsAsFactors=FALSE);pure<-predict_native_model(final,q)*adjustment$factor
    thresholds<-quantile(predict_native_model(final,dev)*adjustment$factor,config$pricing$tier_quantiles,type=7)
    result<-cbind(PolicyID=q$PolicyID,quote_prices(pure,config$pricing,thresholds));write.csv(result,"results/native_r_retrained_quotes.csv",row.names=FALSE)
    write.csv(selected,"results/native_r_selected_candidate.csv",row.names=FALSE)
    saveRDS(list(model=final,calibration=adjustment,thresholds=thresholds,pricing=config$pricing),"results/native_r_retrained_model.rds")
  }
  writeLines(c("Native R research output. Not a fresh test and not a bit-equivalent sklearn refit.",
    "GLM/GAM share the declared penalized log-link/spline objectives. GBM uses native rpart Newton trees; binning/split search differs.",
    "Frozen selected-model scoring remains the cross-language reference tariff. Native R results are separately named and do not overwrite it.",
    capture.output(sessionInfo())),"results/NATIVE_RESEARCH_SCOPE.txt")
  invisible(summary)
}
